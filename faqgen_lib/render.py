"""Render outputs: faq-draft.md, finish-in-claude.md, raw.json.

No FAQ markup is written here. The tool finds questions and candidate
passages, but word matching can't judge whether a passage answers a question:
on a live 39-page run (2026-10-22), 14 of 15 passages it rated as answers
answered a different question. Markup must match verified answers, so it comes
from the finish-in-Claude step (or the /aeo-faq skill), never from this file.
"""

import json
import re
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from .concepts import ConceptGap
from .drafting import FAQDraft
from .page import PageContent
from .serp import Reference


# Drafts with a candidate answer from the client's own site, to be verified.
CANDIDATE = ("answered", "home_here", "linked")


def _status(d: FAQDraft) -> str:
    return d.status or ("gap" if d.page_gap else "answered")


def _format_provenance(refs: List[Reference]) -> str:
    """One line naming who Google cites, for a gap flag."""
    parts = [r.domain or r.url for r in refs if (r.domain or r.url)]
    return ", ".join(parts)


def output_dir_for(url: str, base: Path) -> Path:
    host = urlparse(url).netloc.replace("www.", "")
    slug = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-") or "page"
    out = base / f"{slug}-{date.today().isoformat()}"
    out.mkdir(parents=True, exist_ok=True)
    return out


MARKET_NAMES = {
    "us": "United States", "canada": "Canada", "uk": "United Kingdom",
    "australia": "Australia", "france": "France", "germany": "Germany",
}

# Plain-language reasons, matching the skill's "Needs your decision" wording.
CHECK_REASON = (
    "The tool matches words, so it can't tell whether this passage answers the "
    "question in different words. Read it: if it answers, publish from it; if "
    "not, it's content to write."
)


def render_markdown(
    page: PageContent, drafts: List[FAQDraft],
    intent: str, intent_rationale: str, total_cost: float,
    provenance: Optional[List[Reference]] = None,
    sandbox: bool = False,
    site_notes: Optional[List[str]] = None,
    concept_gaps: Optional[List[ConceptGap]] = None,
    off_topic: Optional[List] = None,
    meta: Optional[dict] = None,
) -> str:
    """faq-draft.md in the same layout as the /aeo-faq skill: actions first,
    then Candidate answers, Needs your decision, Content to write, Appendix."""
    meta = meta or {}
    existing = [d for d in drafts if _status(d) == "exists_here"]
    drafts = [d for d in drafts if _status(d) != "exists_here"]
    ready = [d for d in drafts if _status(d) in CANDIDATE]
    checks = [d for d in drafts if _status(d) == "check"]
    gaps = [d for d in drafts if _status(d) == "gap"]
    concept_gaps = concept_gaps or []
    off_topic = off_topic or []

    lines = [f"# FAQ draft: {page.h1 or page.title}", ""]
    if sandbox:
        lines += [
            "> **SANDBOX OUTPUT, NOT REAL DATA.** Generated against DataForSEO's",
            "> sandbox, which returns placeholder values. The questions and answers",
            "> below are meaningless; this proves the setup works.",
            "> It is not a deliverable. Re-run without `--sandbox` for a real draft.",
            "",
        ]
    market = MARKET_NAMES.get(meta.get("market", ""), meta.get("market", ""))
    queries = " · ".join(meta.get("queries") or []) or "None"
    prompts = " · ".join(meta.get("prompts") or []) or "None given"
    lines += [
        "| | |",
        "|---|---|",
        f"| Page | {page.url} |",
    ]
    if market:
        lines.append(f"| Market | {market}, {meta.get('language', 'en')} |")
    lines += [
        f"| Searches looked up | {queries} |",
        f"| Your prompts | {prompts} |",
        f"| Cost | ${total_cost:.4f} charged |",
        f"| Search intent | {intent} ({intent_rationale}) |",
    ]
    if site_notes is not None:
        lines.append(f"| Pages checked | {' '.join(site_notes)} |")
    lines.append(f"| AI citation data | {meta.get('citations') or 'None'} |")
    lines += [
        "",
        f"**In short:** {len(ready)} candidate answers to verify · {len(gaps)} need new content"
        f" · {len(checks)} need your decision · {len(existing)} already on the page.",
        "",
        "## What to do",
        "",
    ]

    steps = []
    if ready:
        steps.append(
            f"**Verify** the {len(ready)} candidate answers. Each quotes a real passage "
            "from the site, but the tool matches words, so it can't tell whether the "
            "passage answers the question. Paste `finish-in-claude.md` into Claude: it "
            "keeps the passages that answer, writes the final answers and returns FAQ "
            "markup that matches them."
        )
    if checks:
        steps.append(
            f"**Decide** {len(checks)} questions where the tool can't tell whether "
            "the site answers them (see \"Needs your decision\")."
        )
    if gaps or concept_gaps:
        steps.append(
            f"**Write content** for {len(gaps)} questions nothing on the site "
            "answers (see \"Content to write\")."
        )
    for d in drafts:
        for url in d.trim_elsewhere:
            theirs = d.trim_questions.get(url, d.question.question)
            steps.append(
                f"**Edit another page:** on {url}, the question \"{theirs}\" now has "
                "its full answer on this page. Remove the full answer there and keep "
                "at most one sentence linking here; remove the question from that "
                "page's FAQPage markup, if it has any, so the two pages don't "
                "compete for it."
            )
    lines += [f"{i}. {step}" for i, step in enumerate(steps, 1)] or ["Nothing to do."]
    lines.append("")

    if ready:
        lines += ["## Candidate answers: verify before publishing", ""]
        for i, d in enumerate(ready, 1):
            lines += [f"### {i}. {d.question.question}", "", d.answer, ""]
            lines.append(f"- **Asked by:** {_origin(d)}")
            lines.append(f"- **Type:** {_answer_type(d)}")
            if d.home_evidence:
                owner = "the full answer lives there" if _status(d) == "linked" \
                    else "this page carries the full answer"
                lines.append(f"- **Why {owner}:** {d.home_evidence}")
            where = d.source_url or "this page"
            lines.append(
                f"- **Candidate passage** (quoted word for word): "
                f"\"{d.source_excerpt}\" ({where})"
            )
            lines.append("")

    if checks:
        lines += ["## Needs your decision", ""]
        for i, d in enumerate(checks, 1):
            lines += [f"### {i}. {d.question.question}", ""]
            lines.append(f"- **Asked by:** {_origin(d)}")
            lines.append(f"- **Why it can't be published yet:** {CHECK_REASON}")
            if d.near_miss_excerpt:
                lines.append(
                    f"- **What the site says:** \"{d.near_miss_excerpt[:500]}\" "
                    f"({d.source_url or 'this page'})"
                )
            lines.append("")

    if gaps or concept_gaps:
        lines += ["## Content to write", ""]
        cites = _format_provenance(provenance or [])
        for i, d in enumerate(gaps, 1):
            q = d.question
            lines += [f"### {i}. {q.question}", ""]
            lines.append(f"- **Asked by:** {_origin(d)}")
            nothing = "the site" if site_notes is not None else "this page"
            if d.near_miss_excerpt:
                lines.append(
                    f"- **No passage on {nothing} uses this question's key words.** "
                    f"Closest passage: \"{d.near_miss_excerpt[:400]}\" "
                    f"({d.source_url or 'this page'}). It shares some words; read it "
                    "before writing new content."
                )
            else:
                lines.append(
                    f"- **No passage on {nothing} shares this question's words.**")
            if q.source_domain:
                lines.append(
                    f"- **Who Google shows instead:** {q.source_url or q.source_domain}")
            elif cites:
                lines.append(f"- **Who Google shows instead:** {cites}")
            lines.append("")
        if concept_gaps:
            lines += [
                "### Topics competitors cover that this page doesn't",
                "",
                "Ideas, not Google questions: turn one into an FAQ question only if "
                "the client can answer it from their own material.",
                "",
            ]
            for gap in concept_gaps:
                where = f" (the client covers it on {gap.covered_at})" if gap.covered_at else ""
                lines.append(f"- {gap.concept}: {', '.join(gap.competitors)}{where}")
            lines.append("")

    lines += ["## Appendix", ""]
    if existing:
        lines.append("- **Already answered on this page:** " + " · ".join(
            f"{d.question.question} (the page asks \"{d.existing_question}\")"
            for d in existing))
    if off_topic:
        lines.append("- **Left out as off-topic** (no words in common with the "
                     "searches, title or H1): "
                     + " · ".join(q.question for q in off_topic))
    lines.append(
        f"- **Sources:** search data from DataForSEO ({market or 'market not set'}, "
        f"{date.today().isoformat()}); pages read: {page.url}"
        + (f"; {' '.join(site_notes)}" if site_notes else "")
    )
    lines.append(
        "- **FAQ markup:** not produced here; it must match verified answers, so "
        "`finish-in-claude.md` asks Claude to return it."
    )
    lines.append("")
    return "\n".join(lines)


def _answer_type(d: FAQDraft) -> str:
    status = _status(d)
    if status == "linked":
        return (f"Short answer on this page; the full answer stays on {d.link_url}. "
                f"Nothing changes on {d.link_url}.")
    if status == "home_here" and d.source_url in d.trim_questions:
        theirs = d.trim_questions[d.source_url]
        return (f"Full answer on this page, moved from {d.source_url}, which publishes "
                f"the same question (\"{theirs}\"). On {d.source_url}: remove the full "
                "answer, keep at most one sentence linking here, and remove the "
                "question from that page's FAQPage markup, if it has any (see "
                "What to do).")
    if status == "home_here":
        return (f"Full answer on this page, written from the text of {d.source_url}. "
                "That page has no question on this, so nothing changes there.")
    if d.trim_elsewhere:
        return ("Full answer on this page, from this page's own text. Other pages "
                "that publish this question hand it over (see What to do).")
    return "Full answer on this page, from this page's own text."


def _origin(d: FAQDraft) -> str:
    q = d.question
    google = [k for k in d.surfaced_by if k and k != "your prompt"]
    if q.from_prompt:
        origin = f"your prompt \"{q.prompt}\""
        return origin + (f" · Google also asks it for \"{google[0]}\"" if google else "")
    searches = " and ".join(f"\"{k}\"" for k in google)
    if q.from_related:
        return f"Google related search for {searches}"
    return f"Google, when people search {searches} (question #{q.rank})"


def render_claude_prompt(
    page: PageContent, drafts: List[FAQDraft],
    concept_gaps: Optional[List[ConceptGap]] = None,
) -> str:
    """A paste-ready prompt that turns the extracts into final answers and
    returns FAQ markup matching the polished text."""
    lines = [
        "Paste everything below into Claude (or ChatGPT) to turn the drafts into",
        "verified FAQ answers and the FAQ markup that matches them.",
        "",
        "---",
        "",
        f"You are writing the FAQ section for {page.url} ({page.title}).",
        "",
        "The CANDIDATE passages below were matched by words, not meaning: many",
        "answer a different question. Check each one before using it.",
        "",
        "Rules:",
        "1. Use ONLY the source excerpts below. Nothing from memory or the web.",
        "2. For each CANDIDATE, first decide whether the passage answers the",
        "   question asked. If it doesn't, mark it CONTENT TO WRITE. If it does,",
        "   write the answer as 2-3 sentences that lead with the direct",
        "   answer. Under it, give one supporting quote per sentence, copied",
        "   exactly from the excerpts, in the same order.",
        "3. Cut any sentence you can't support with a quote, including general",
        "   framing the excerpts never say. If nothing is left, mark the question",
        "   CONTENT TO WRITE.",
        "4. Numbers, prices, durations, percentages and credentials must appear",
        "   exactly as in a quote. Don't round or rephrase them.",
        "5. If two excerpts disagree on a fact, don't answer: mark the question",
        "   NEEDS YOUR DECISION and quote both versions.",
        "6. SHORT + LINK: the full answer lives on the linked page. Write one or",
        "   two sentences, worded differently from that page, ending with a link",
        "   to it.",
        "7. MOVED HERE: this page is the better home; write the full answer here",
        "   from the excerpt, which comes from another page.",
        "8. CHECK: decide whether the passage really answers the question. If it",
        "   does, write the answer from it alone (rules 2-5); if not, mark it",
        "   CONTENT TO WRITE.",
        "9. CONTENT TO WRITE questions stay unanswered. Say in one line what the",
        "   page would need to answer them.",
        "10. Questions from the user's own prompts may be rephrased as natural FAQ",
        "    questions, keeping exactly what they ask.",
        "",
        "Return, in this order:",
        "A. \"Ready to publish\": each question, its final answer, its quotes.",
        "B. \"Needs your decision\" and \"Content to write\": each question with a",
        "   one-line reason.",
        "C. FAQPage JSON-LD containing only section A, with each answer's text",
        "   exactly as written in A.",
        "",
    ]
    labels = {"answered": "CANDIDATE", "home_here": "CANDIDATE, MOVED HERE",
              "linked": "CANDIDATE, SHORT + LINK"}
    for i, d in enumerate(drafts, 1):
        status = _status(d)
        if status == "exists_here":
            continue
        lines.append(f"### Question {i}: {d.question.question}")
        if d.question.from_prompt:
            lines.append(f"From the user's prompt: {d.question.prompt}")
        if status == "gap":
            lines.append("CONTENT TO WRITE: no source material on the page or site.")
        elif status == "check":
            lines.append(f"CHECK. Passage ({d.source_url or page.url}): {d.near_miss_excerpt}")
        else:
            label = labels.get(status, "CANDIDATE")
            if status == "linked":
                label += f", full answer at {d.link_url}"
            elif status == "home_here":
                label += f", excerpt from {d.source_url}"
            lines.append(label)
            lines.append(f"Draft answer: {d.answer}")
            lines.append(f"Source excerpt ({d.source_url or page.url}): {d.source_excerpt}")
        lines.append("")
    if concept_gaps:
        lines += [
            "### Topics competitors cover that this page doesn't",
            "For each, say whether it fits this page. Suggest an FAQ question only",
            "where the excerpts above already hold the answer; otherwise list it",
            "under CONTENT TO WRITE. Never answer from competitor knowledge.",
            "",
        ]
        lines += [f"- {g.concept} ({', '.join(g.competitors)})" for g in concept_gaps]
        lines.append("")
    return "\n".join(lines)


def write_outputs(
    out_dir: Path, page: PageContent, drafts: List[FAQDraft],
    intent: str, intent_rationale: str, total_cost: float,
    raw_responses: Dict[str, dict],
    provenance: Optional[List[Reference]] = None,
    sandbox: bool = False,
    site_notes: Optional[List[str]] = None,
    concept_gaps: Optional[List[ConceptGap]] = None,
    off_topic: Optional[List] = None,
    meta: Optional[dict] = None,
) -> List[Path]:
    files = {
        "faq-draft.md": render_markdown(
            page, drafts, intent, intent_rationale, total_cost, provenance, sandbox,
            site_notes, concept_gaps, off_topic, meta,
        ),
        "finish-in-claude.md": render_claude_prompt(page, drafts, concept_gaps),
        "raw.json": json.dumps(raw_responses, indent=2, ensure_ascii=False),
    }
    written = []
    for name, content in files.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
