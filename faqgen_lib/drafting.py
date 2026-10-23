"""Question prioritization and extractive answer drafting.

Answers are extracted verbatim from the page (2-3 sentences) so they are
correct by construction. When the page has no material for a question, the
question is flagged as a PAGE GAP rather than answered — never fabricate.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .page import PageContent
from .serp import PAAQuestion

# Minimum share of a question's content tokens that a paragraph must contain
# (and at least 2 matched tokens) before we treat it as answer material.
ANSWER_OVERLAP_THRESHOLD = 0.45
_MIN_MATCHED_TOKENS = 2
_MIN_PARAS_FOR_TOPIC_WORDS = 3

_STOPWORDS = {
    # Question scaffolding carries no topic meaning: "how much does X cost"
    # is about cost, not about "much".
    "much", "many", "get", "got", "make", "made", "need", "needs", "use", "used",
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "could",
    "do", "does", "did", "for", "from", "has", "have", "how", "i", "if", "in",
    "is", "it", "its", "me", "my", "of", "on", "or", "our", "should", "than",
    "that", "the", "their", "there", "they", "this", "to", "was", "we", "were",
    "what", "when", "where", "which", "who", "why", "will", "with", "would",
    "you", "your",
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# "What is X?", "What does X do?", "How does X work?": answered by a sentence
# that defines X, which word matching can't tell from any sentence that merely
# mentions X. Excludes "what is the best/difference/disadvantage…" shapes,
# which carry a distinctive word of their own.
_DEFINITIONAL = re.compile(
    r"^(?:what\s+(?:is|are)\s+"
    r"(?!(?:(?:an?|the)\s+)?(?:best|main|key|most|top|biggest|difference|benefits?|"
    r"advantages?|disadvantages?|downsides?|risks?|types?|examples?|"
    r"ways?|cost|price|purpose|role)\b)"
    r"[\w\s'’-]+\??$"
    r"|what\s+does\s+.+\s+do\b.*"
    r"|how\s+(?:does|do)\s+.+\s+work\b.*)",
    re.I,
)


# Spelling variants that should match: some words have two spellings in
# common use, and Canadian and British sites use spellings that searches
# often don't. A question spelled one way was dropped as off-topic for a page
# spelled the other way before this (live run, 2026-10-22).
_VARIANTS = {
    "dietician": "dietitian", "behavior": "behaviour", "behavioral": "behavioural",
    "fiber": "fibre", "center": "centre", "program": "programme",
}


def _stem(word: str) -> str:
    # light plural normalization so "cost" matches "costs", "tub" matches "tubs"
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def tokenize(text: str) -> Set[str]:
    # Numbers are kept whole and at any length: "60-30-10" and "15" are often
    # the one word that makes a question distinctive. Dropping them once let a
    # question about one numbered rule match a paragraph about a different
    # rule (live run, 2026-10-22).
    words = re.findall(r"\d+(?:[-/.]\d+)+|[a-z0-9]+(?:'[a-z]+)?", text.lower())
    return {
        _VARIANTS.get(_stem(w), _stem(w)) for w in words
        if w[0].isdigit() or (w not in _STOPWORDS and len(w) > 2)
    }


def best_sentences(paragraph: str, q_tokens: Set[str], n: int) -> str:
    """The n sentences sharing most words with the question, in page order."""
    sentences = [x for x in _SENTENCE_SPLIT.split(paragraph.strip()) if x]
    if len(sentences) <= n:
        return " ".join(sentences).strip()
    ranked = sorted(
        range(len(sentences)),
        key=lambda i: (-len(q_tokens & tokenize(sentences[i])), i),
    )[:n]
    return " ".join(sentences[i] for i in sorted(ranked)).strip()


def same_meaning(a: str, b: str, threshold: float = 0.6) -> bool:
    """Two questions asking the same thing, however worded."""
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def split_off_topic(
    candidates: List[PAAQuestion], page: PageContent, keywords: List[str]
) -> tuple:
    """(kept, dropped): Google questions sharing no word with the page's
    queries, title or H1 are dropped. The user's prompts are always kept.

    Head terms pull in consumer questions that share no word with the page
    ("Which organ dies first before death?" for a product name); a page
    can't use those.
    """
    topic = tokenize(" ".join([*keywords, page.title, page.h1]))
    kept, dropped = [], []
    for q in candidates:
        if q.from_prompt or tokenize(q.question) & topic:
            kept.append(q)
        else:
            dropped.append(q)
    return kept, dropped


@dataclass
class FAQDraft:
    question: PAAQuestion
    score: float                    # prioritization score
    answer: str = ""                # extracted draft (empty when page_gap)
    source_excerpt: str = ""        # paragraph the answer came from
    source_heading: str = ""        # nearest heading above that paragraph
    page_gap: bool = False
    surfaced_by: List[str] = field(default_factory=list)  # keywords
    # Closest passage when the page touches the topic but doesn't answer it —
    # useful for a content brief, never publishable as an answer.
    near_miss_excerpt: str = ""
    near_miss_heading: str = ""
    # Site-wide placement (see site.place_answer). Without a site check every
    # draft is simply "answered" or "gap".
    status: str = ""
    source_url: str = ""            # set when the excerpt is from another page
    link_url: str = ""              # the full answer's home, for "linked"
    # Other pages that publish this question as a Q&A, and their wording of it.
    # The Q&A moves here: they cut it to a linking sentence and drop its markup.
    trim_elsewhere: List[str] = field(default_factory=list)
    trim_questions: Dict[str, str] = field(default_factory=dict)
    existing_question: str = ""     # the page's own wording, for "exists_here"
    home_evidence: str = ""         # why this page (or the linked one) owns it


def prioritize_questions(
    candidates: List[PAAQuestion], page: PageContent, max_questions: int
) -> List[FAQDraft]:
    """Dedupe and rank PAA questions; multi-keyword > PAA rank > page overlap."""
    page_tokens = tokenize(f"{page.title} {page.h1} {page.meta_description}")

    merged: Dict[str, FAQDraft] = {}
    for q in candidates:
        key = re.sub(r"[^a-z0-9 ]", "", q.question.lower()).strip()
        if not key:
            continue
        if key not in merged:
            # Same question, different wording: "the" vs "a" disadvantage.
            key = next(
                (k for k, d in merged.items() if same_meaning(q.question, d.question.question)),
                key,
            )
        if key in merged:
            draft = merged[key]
            if q.keyword not in draft.surfaced_by:
                draft.surfaced_by.append(q.keyword)
            # keep the richest provenance (an expanded answer beats none)
            if q.answer_text and not draft.question.answer_text:
                draft.question = q
        else:
            merged[key] = FAQDraft(question=q, score=0.0, surfaced_by=[q.keyword])

    for draft in merged.values():
        q = draft.question
        overlap = len(tokenize(q.question) & page_tokens)
        draft.score = (
            3.0 * (len(draft.surfaced_by) - 1)          # asked under several keywords
            + max(0.0, 10 - q.rank) * 0.2               # earlier in the PAA block
            + overlap * 0.5                             # topical fit with the page
            - (1.0 if q.from_related else 0.0)          # PAA beats related searches
            + (100.0 - q.rank if q.from_prompt else 0.0)  # the user's own prompts lead
        )

    ranked = sorted(merged.values(), key=lambda d: -d.score)
    return ranked[:max_questions]


def draft_answer(draft: FAQDraft, page: PageContent) -> FAQDraft:
    """Fill in an extractive answer, or flag a PAGE GAP if the page lacks material.

    Tokens that appear in most of the page's paragraphs (the page's own topic
    words) are down-weighted, so coverage must come from the tokens that make
    the question distinctive — a pricing paragraph can't "answer" a Medicare
    question just because both mention the product.
    """
    q_tokens = tokenize(draft.question.question)
    if not q_tokens:
        draft.page_gap = True
        draft.status = "gap"
        return draft

    para_tokens = [tokenize(p) for p, _ in page.paragraphs]
    n_paras = max(len(para_tokens), 1)

    def weight(token: str) -> float:
        # "Repeated everywhere" means nothing on a one- or two-paragraph page,
        # where every word would count; there, every word must match instead.
        if n_paras < _MIN_PARAS_FOR_TOPIC_WORDS:
            return 1.0
        doc_freq = sum(1 for toks in para_tokens if token in toks)
        return 0.5 if doc_freq > n_paras / 2 else 1.0

    total_weight = sum(weight(t) for t in q_tokens)

    # The tokens that carry the question's actual meaning: those the page does
    # NOT repeat everywhere. "What are the drawbacks of a walk-in tub?" on a
    # page all about walk-in tubs comes down to "drawbacks" — if that word is
    # absent, no amount of matching on "walk" and "tub" makes a paragraph an
    # answer. Requiring one of these to hit is what separates a real answer
    # from a same-topic near-miss.
    # Every one of them must appear. Matching some of them is what a
    # same-topic paragraph does; matching all of them is what an answer does.
    distinctive = {t for t in q_tokens if weight(t) == 1.0}
    # When every word of the question is the page's own topic ("What does a
    # walk-in tub do in a bathroom?" on a walk-in tub article), nothing is
    # left to test and any on-topic sentence would pass. Don't guess: offer
    # the closest passage and leave the call to a person (or Claude).
    # Seen on a live run 2026-10-22: three such questions got confident non-answers.
    if not distinctive:
        draft.page_gap = True
        draft.status = "check"
        scored = [
            (len(q_tokens & toks), paragraph, heading)
            for (paragraph, heading), toks in zip(page.paragraphs, para_tokens)
        ]
        best_try = max(scored, default=None)
        if best_try and best_try[0] >= _MIN_MATCHED_TOKENS:
            draft.near_miss_excerpt = best_sentences(best_try[1], q_tokens, 3)
            draft.near_miss_heading = best_try[2] or ""
        return draft

    best: Optional[tuple] = None       # (coverage, paragraph, heading)
    near_miss: Optional[tuple] = None  # best paragraph that failed the gate
    for (paragraph, heading), toks in zip(page.paragraphs, para_tokens):
        matched = q_tokens & toks
        if len(matched) < _MIN_MATCHED_TOKENS:
            continue
        coverage = sum(weight(t) for t in matched) / total_weight
        if distinctive and not distinctive.issubset(matched):
            if near_miss is None or coverage > near_miss[0]:
                near_miss = (coverage, paragraph, heading)
            continue
        if best is None or coverage > best[0]:
            best = (coverage, paragraph, heading)

    if best is None or best[0] < ANSWER_OVERLAP_THRESHOLD:
        draft.page_gap = True
        draft.status = "gap"
        if near_miss:
            draft.near_miss_excerpt = near_miss[1]
            draft.near_miss_heading = near_miss[2] or ""
        return draft

    _, paragraph, heading = best
    if _DEFINITIONAL.match(draft.question.question.strip()):
        draft.page_gap = True
        draft.status = "check"
        draft.near_miss_excerpt = best_sentences(paragraph, q_tokens, 3)
        draft.near_miss_heading = heading or ""
        return draft
    draft.answer = best_sentences(paragraph, q_tokens, 3)
    draft.source_excerpt = paragraph
    draft.source_heading = heading or ""
    draft.status = "answered"
    return draft
