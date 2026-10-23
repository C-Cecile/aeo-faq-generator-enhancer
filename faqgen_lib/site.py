"""Site-wide check: which questions the rest of the client's site already answers.

An FAQ that repeats another page's answer at full length competes with that
page for the same query. So every candidate question gets a "home": the page
most centred on its topic. The home page carries the full answer; any other
page that touches the question gets a short, differently worded answer that
links to the home. A general FAQ hub is never the home when a topical page
answers the question too — it is the page that should shorten and link.

Answers here still come only from the client's own site. Competitor content
never becomes answer text.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from .drafting import FAQDraft, best_sentences, draft_answer, same_meaning, tokenize
from .page import PageContent, fetch_html, parse_html
from .citations import MIN_EVIDENCE, page_key
from .serp import PAAQuestion

Fetcher = Callable[[str], Optional[str]]

FAQ_PATHS = ("/faq", "/faqs", "/frequently-asked-questions")
_FAQ_TEXT = re.compile(r"\bfaqs?\b|frequently asked", re.I)
_NON_HTML = re.compile(r"\.(?:jpe?g|png|gif|webp|svg|pdf|zip|mp4|xml|css|js)$", re.I)
_LANG_PREFIX = re.compile(r"^/([a-z]{2}(?:-[a-z]{2})?)(?:/|$)", re.I)
_SAME_QUESTION_JACCARD = 0.6
_MAX_SITEMAP_URLS = 5000
_MAX_CHILD_SITEMAPS = 10


@dataclass
class SitePage:
    page: PageContent
    is_faq_hub: bool = False


@dataclass
class Coverage:
    """One place on the site that already answers a question."""
    url: str
    excerpt: str
    relevance: float               # how centred the page is on the question
    is_faq_hub: bool = False
    existing_question: str = ""    # set when the page asks the question itself


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    return f"{host}{parsed.path.rstrip('/') or '/'}"


def _same_site(url: str, base_url: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return host == urlparse(base_url).netloc.lower().removeprefix("www.")


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def looks_like_faq_hub(url: str, page: Optional[PageContent] = None) -> bool:
    if _FAQ_TEXT.search(urlparse(url).path.replace("-", " ")):
        return True
    return bool(page and _FAQ_TEXT.search(f"{page.title} {page.h1}"))


def find_faq_links(page: PageContent) -> List[str]:
    """Same-site links whose URL or anchor text names an FAQ."""
    found = []
    for href, text in page.links:
        if not _same_site(href, page.url):
            continue
        if _FAQ_TEXT.search(text) or looks_like_faq_hub(href):
            if normalize_url(href) != normalize_url(page.url) and href not in found:
                found.append(href)
    return found


def _locs(xml_text: str) -> List[str]:
    try:
        root = ET.fromstring(xml_text.strip().encode("utf-8"))
    except ET.ParseError:
        return []
    return [
        el.text.strip() for el in root.iter()
        if el.tag.endswith("loc") and el.text and el.text.strip()
    ]


def sitemap_urls(base_url: str, fetch: Fetcher) -> List[str]:
    """Page URLs from robots.txt sitemaps, else /sitemap.xml or /sitemap_index.xml."""
    origin = _origin(base_url)
    roots = []
    robots = fetch(f"{origin}/robots.txt") or ""
    for line in robots.splitlines():
        if line.lower().startswith("sitemap:"):
            roots.append(line.split(":", 1)[1].strip())
    if not roots:
        roots = [f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml"]

    pages: List[str] = []
    children_fetched = 0
    queue = list(roots)
    seen = set()
    while queue and len(pages) < _MAX_SITEMAP_URLS:
        sitemap = queue.pop(0)
        if sitemap in seen:
            continue
        seen.add(sitemap)
        xml_text = fetch(sitemap)
        if not xml_text:
            continue
        if sitemap == f"{origin}/sitemap.xml" and f"{origin}/sitemap_index.xml" in queue:
            queue.remove(f"{origin}/sitemap_index.xml")  # usually the same pages
        for loc in _locs(xml_text):
            if loc.lower().endswith(".xml") or ".xml?" in loc.lower():
                if children_fetched < _MAX_CHILD_SITEMAPS:
                    queue.append(loc)
                    children_fetched += 1
            elif _same_site(loc, base_url) and not _NON_HTML.search(loc):
                pages.append(loc)
    return pages[:_MAX_SITEMAP_URLS]


def language_prefix(url: str) -> str:
    """'fr' for /fr/blog/x, '' for an unprefixed path."""
    match = _LANG_PREFIX.match(urlparse(url).path)
    return match.group(1).lower() if match else ""


def _slug_tokens(url: str) -> set:
    return tokenize(urlparse(url).path.replace("-", " ").replace("_", " ").replace("/", " "))


def rank_related_urls(
    urls: Iterable[str], topic_terms: Sequence[str], exclude: Iterable[str], limit: int,
    language: Optional[str] = None,
) -> List[str]:
    """Sitemap URLs whose slug shares the most words with the topic."""
    topic = set()
    for term in topic_terms:
        topic |= tokenize(term)
    skip = {normalize_url(u) for u in exclude}
    scored = []
    for url in urls:
        if normalize_url(url) in skip:
            continue
        # Translations answer the same questions in another language; they
        # are not competing pages for this one.
        if language is not None and language_prefix(url) != language:
            continue
        overlap = len(_slug_tokens(url) & topic)
        if overlap:
            scored.append((overlap, -len(url), url))
    scored.sort(reverse=True)
    out, seen = [], set()
    for _, _, url in scored:
        key = normalize_url(url)
        if key not in seen:
            seen.add(key)
            out.append(url)
        if len(out) >= limit:
            break
    return out


def detect_faq_hubs(target: PageContent, fetch: Fetcher) -> List[str]:
    """General FAQ pages: linked from the target page, else common paths."""
    links = find_faq_links(target)
    if links:
        return links[:3]
    origin = _origin(target.url)
    return [f"{origin}{path}" for path in FAQ_PATHS if fetch(f"{origin}{path}")][:1]


def load_site_pages(
    target: PageContent,
    keywords: Sequence[str],
    faq_urls: Optional[Sequence[str]] = None,
    extra_urls: Sequence[str] = (),
    max_related: int = 12,
    fetch: Fetcher = fetch_html,
) -> Tuple[List[SitePage], List[str]]:
    """Fetch the FAQ hub(s), named pages and the most related sitemap pages.

    ``faq_urls`` None means detect the hub; an empty list means the user said
    the site has none. Returns (pages, notes) — notes say what was checked,
    for the draft header.
    """
    notes = []
    hubs = list(faq_urls) if faq_urls is not None else detect_faq_hubs(target, fetch)
    notes.append(
        f"General FAQ checked: {', '.join(hubs)}" if hubs
        else "No general FAQ page found (none linked from the page, none at /faq or /faqs)."
    )

    listed = sitemap_urls(target.url, fetch)
    related = rank_related_urls(
        listed, [*keywords, target.h1 or target.title],
        exclude=[target.url, *hubs, *extra_urls], limit=max_related,
        language=language_prefix(target.url),
    )
    notes.append(
        f"Sitemap: {len(listed)} pages listed, {len(related)} topically related pages checked."
        if listed else "No sitemap found — only the FAQ page and pages you named were checked."
    )

    pages, seen = [], {normalize_url(target.url)}
    for url in [*hubs, *extra_urls, *related]:
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        html = fetch(url)
        if not html:
            continue
        page = parse_html(url, html)
        if page.paragraphs or page.qa_pairs:
            is_hub = url in hubs or looks_like_faq_hub(url, page)
            pages.append(SitePage(page=page, is_faq_hub=is_hub))
    return pages, notes


def same_question(a: str, b: str) -> bool:
    return same_meaning(a, b, _SAME_QUESTION_JACCARD)


def topical_relevance(question: str, page: PageContent, is_faq_hub: bool) -> float:
    """Share of the question's words in the page's title, H1 and URL.

    A general FAQ hub is about everything, so it scores zero: it only becomes
    a question's home when no topical page answers it.
    """
    if is_faq_hub:
        return 0.0
    q = tokenize(question)
    if not q:
        return 0.0
    topic = tokenize(f"{page.title} {page.h1}") | _slug_tokens(page.url)
    return len(q & topic) / len(q)


def find_coverage(
    question: str, site_pages: Sequence[SitePage], maybe: Optional[list] = None,
) -> List[Coverage]:
    """Every other page that already answers the question, best home first.

    Pages that might answer it but need a person to judge (status "check")
    are appended to ``maybe`` when given.
    """
    found = []
    for sp in site_pages:
        relevance = topical_relevance(question, sp.page, sp.is_faq_hub)
        match = next(
            ((q, a) for q, a in sp.page.qa_pairs if same_question(question, q)), None
        )
        if match:
            found.append(Coverage(
                url=sp.page.url, excerpt=match[1], relevance=relevance,
                is_faq_hub=sp.is_faq_hub, existing_question=match[0],
            ))
            continue
        probe = draft_answer(
            FAQDraft(question=PAAQuestion(question=question, rank=0), score=0.0),
            sp.page,
        )
        if not probe.page_gap:
            found.append(Coverage(
                url=sp.page.url, excerpt=probe.source_excerpt,
                relevance=relevance, is_faq_hub=sp.is_faq_hub,
            ))
        elif probe.status == "check" and maybe is not None and probe.near_miss_excerpt:
            maybe.append(Coverage(
                url=sp.page.url, excerpt=probe.near_miss_excerpt,
                relevance=relevance, is_faq_hub=sp.is_faq_hub,
            ))
    found.sort(key=lambda c: -c.relevance)
    return found


_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _sentences(text: str, n: int) -> str:
    return " ".join(_SENTENCE.split(text.strip())[:n]).strip()


def place_answer(
    draft: FAQDraft, target: PageContent, site_pages: Sequence[SitePage],
    citations=None,
) -> FAQDraft:
    """Decide where the full answer lives and what this page should carry.

    Sets ``draft.status``:
      exists_here — the target page already asks and answers it; not redrafted
      answered    — this page is the home; full answer from this page
      home_here   — this page is the home, but the material is on another page
      linked      — another page is the home; short answer + link to it
      gap         — nothing on the site answers it

    With AI citation data (``citations.CitationData``), the home is the page
    assistants already cite on unbranded prompts about the question, when
    there is enough evidence; otherwise the page whose title, H1 and URL are
    most centred on it. Either way the reason is recorded in
    ``draft.home_evidence``.
    """
    question = draft.question.question
    existing = next((q for q, _ in target.qa_pairs if same_question(question, q)), None)
    if existing:
        draft.status, draft.existing_question = "exists_here", existing
        draft.page_gap = False
        return draft

    maybe: List[Coverage] = []
    elsewhere = find_coverage(question, site_pages, maybe)
    if draft.status != "check":
        draft.status = "gap" if draft.page_gap else "answered"
    if not elsewhere:
        if draft.status == "gap" and maybe:
            # Another page might answer it; only a person can say.
            best_maybe = max(maybe, key=lambda c: c.relevance)
            draft.status = "check"
            draft.near_miss_excerpt = best_maybe.excerpt
            draft.near_miss_heading = ""
            draft.source_url = best_maybe.url
        return draft

    here = topical_relevance(question, target, looks_like_faq_hub(target.url, target))
    evidence = citations.cited_pages(question, target.url) if citations else None
    total = sum(evidence.values()) if evidence else 0
    if total >= MIN_EVIDENCE:
        # Build on the page assistants already cite rather than moving the
        # answer to one they don't.
        here = evidence[page_key(target.url)] / total
        for c in elsewhere:
            c.relevance = evidence[page_key(c.url)] / total
        elsewhere.sort(key=lambda c: -c.relevance)

    def because(url: str) -> str:
        if total < MIN_EVIDENCE:
            return "its title, heading and URL are the closest match to this question"
        n = evidence[page_key(url)]
        return (f"AI assistants cite it {n} of {total} times on unbranded prompts "
                f"about this (citation data)")

    best = elsewhere[0]
    if best.relevance > here:
        # Another page owns this topic: keep this page's answer short and
        # point to the full one there, so the two don't compete.
        material = draft.source_excerpt if not draft.page_gap else best.excerpt
        draft.status, draft.page_gap = "linked", False
        draft.answer = best_sentences(material, tokenize(question), 1)
        draft.link_url = best.url
        draft.home_evidence = because(best.url)
        draft.near_miss_excerpt = draft.near_miss_heading = ""
        if draft.source_excerpt == "":
            draft.source_excerpt = best.excerpt
            draft.source_url = best.url
        return draft

    # This page is the home. Draft the full answer here (from another page's
    # material when this page has none). Pages that publish the question as a
    # Q&A hand it over; body text elsewhere stays as it is.
    if draft.page_gap:
        draft.status, draft.page_gap = "home_here", False
        draft.answer = best_sentences(best.excerpt, tokenize(question), 3)
        draft.source_excerpt = best.excerpt
        draft.source_url = best.url
        draft.near_miss_excerpt = draft.near_miss_heading = ""
    published = [c for c in elsewhere if c.existing_question]
    draft.trim_elsewhere = [c.url for c in published]
    draft.trim_questions = {c.url: c.existing_question for c in published}
    draft.home_evidence = because(target.url)
    return draft
