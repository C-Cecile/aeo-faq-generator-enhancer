"""Concept gaps: what the top-ranking competitors cover that the page does not.

The pages that rank for a query are what Google's "People also ask" box and AI
answers draw on, so topics they share and the target page lacks are questions
the page can't win yet. Concepts are read from competitors' H2/H3 headings,
which name a section's topic in a few words and need no entity API.

These are a second, labelled source of FAQ ideas — not Google questions and
never answer material. Competitor text only says what to cover; what the
client says about it must come from the client.
"""

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence
from urllib.parse import urlparse

from .drafting import tokenize
from .page import PageContent, fetch_html, parse_html
from .serp import OrganicResult

Fetcher = Callable[[str], Optional[str]]

# Results that are not a competing page about the topic.
_SKIP_DOMAINS = re.compile(
    r"(?:reddit|quora|youtube|youtu\.be|facebook|instagram|tiktok|pinterest|"
    r"linkedin|twitter|x\.com|amazon|ebay|wikipedia)\.", re.I,
)
# Headings that are page furniture rather than topic.
_BOILERPLATE = re.compile(
    r"\b(?:related|share|comments?|subscribe|newsletter|contact|about (?:us|the author)|"
    r"table of contents|sources|references|follow us|recent posts|leave a reply|"
    r"sign up|you may also|you might also|read more|categories|tags|recently viewed|"
    r"similar content|navigation|menu|search|cart|cookies?|privacy|"
    r"introduction|methods|results|discussion|conclusions?|abstract|"
    r"acknowledg\w*|funding|author information|cited by|footnotes|permissions|"
    r"declarations?|competing interests|conflicts? of interest|data availability|"
    r"before you (?:begin|start)|supplementary|artificial intelligence|"
    r"(?:table|figure|fig)\.?\s*\d+)\b", re.I,
)
# "Mara Lindqvist", "Tomas R Feld , PhD": bylines, not topics.
_PERSON = re.compile(
    r"^(?:Dr\.?\s+)?[A-Z][a-z]+(?:\s+[A-Z]\.?)?"
    r"(?:\s+(?:(?:da|de|del|do|dos|van|von|der|la|le)\s+)?[A-Z][a-z'-]+){1,3}"
    r"(?:\s*,?\s*(?:PhD|MD|MSc|MS|BS|BSc|MA|MBA|DPhil|RN))?$"
)
_NUMBERED_SECTION = re.compile(r"^\d+(?:\.\d+)*\.?\s")
_COVERED_SHARE = 0.6
# A concept only one competitor covers is a weaker signal; cap how many each
# competitor contributes so one long page can't fill the list.
_MAX_SINGLE_SOURCE_PER_COMPETITOR = 2
_MERGE_JACCARD = 0.5


@dataclass
class ConceptGap:
    concept: str                              # the clearest heading wording
    tokens: set
    competitors: List[str] = field(default_factory=list)   # domains
    headings: List[str] = field(default_factory=list)      # all wordings seen
    covered_at: str = ""                       # another client page covering it
    order: int = 0                             # heading position on its first page
    on_topic: bool = False                     # shares a word with the page's topic


def _covered(tokens: set, pages: Sequence[PageContent]) -> Optional[str]:
    """URL of the first page with a heading or paragraph covering the concept."""
    for page in pages:
        texts = [h for _, h in page.headings] + [p for p, _ in page.paragraphs]
        for text in texts:
            if len(tokens & tokenize(text)) / len(tokens) >= _COVERED_SHARE:
                return page.url
    return None


def competitor_pages(
    organic: Sequence[OrganicResult], client_url: str, limit: int = 5,
    fetch: Optional[Fetcher] = None,
) -> List[PageContent]:
    client_host = urlparse(client_url).netloc.lower().removeprefix("www.")
    pages, domains = [], set()
    for result in sorted(organic, key=lambda r: r.rank):
        domain = (result.domain or urlparse(result.url).netloc).lower().removeprefix("www.")
        if not result.url or domain == client_host or domain in domains:
            continue
        if _SKIP_DOMAINS.search(domain):
            continue
        html = (fetch or fetch_html)(result.url)
        if not html:
            continue
        domains.add(domain)
        pages.append(parse_html(result.url, html))
        if len(pages) >= limit:
            break
    return pages


def find_concept_gaps(
    target: PageContent, competitors: Sequence[PageContent],
    keywords: Sequence[str], site_pages: Sequence[PageContent] = (),
    limit: int = 8,
) -> List[ConceptGap]:
    """Competitor section topics the target page doesn't cover, most shared first."""
    keyword_tokens = set()
    for kw in keywords:
        keyword_tokens |= tokenize(kw)
    page_topic = keyword_tokens | tokenize(f"{target.title} {target.h1}")

    gaps: List[ConceptGap] = []
    for comp in competitors:
        domain = urlparse(comp.url).netloc.lower().removeprefix("www.")
        brand = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])
        brand = brand if len(brand) >= 5 else ""
        order = 0
        for tag, heading in comp.headings:
            if tag not in ("h2", "h3") or _BOILERPLATE.search(heading):
                continue
            if _PERSON.match(heading.strip()) or _NUMBERED_SECTION.match(heading):
                continue
            if brand and brand in re.sub(r"[^a-z0-9]", "", heading.lower()):
                continue  # about the competitor's business, not the topic
            if not 2 <= len(heading.split()) <= 12:
                continue
            heading_tokens = tokenize(heading)
            tokens = heading_tokens - keyword_tokens
            if not tokens or _covered(tokens, [target]):
                continue
            on_topic = bool(heading_tokens & page_topic)
            order += 1
            match = next(
                (g for g in gaps
                 if len(g.tokens & tokens) / len(g.tokens | tokens) >= _MERGE_JACCARD),
                None,
            )
            if match:
                if domain not in match.competitors:
                    match.competitors.append(domain)
                if heading not in match.headings:
                    match.headings.append(heading)
            else:
                gaps.append(ConceptGap(
                    concept=heading, tokens=tokens,
                    competitors=[domain], headings=[heading], order=order,
                    on_topic=on_topic,
                ))

    # Shared concepts first; ties go to each competitor's leading sections in
    # turn rather than all of the first competitor's.
    gaps.sort(key=lambda g: (-len(g.competitors), g.order))
    kept, per_competitor = [], {}
    for gap in gaps:
        if len(gap.competitors) == 1:
            # One competitor's heading that shares no word with the page's
            # topic is usually that page's own sidetrack ("Partnering for
            # Success", "TB Management"), not a gap in this page.
            if not gap.on_topic:
                continue
            seen = per_competitor.get(gap.competitors[0], 0)
            if seen >= _MAX_SINGLE_SOURCE_PER_COMPETITOR:
                continue
            per_competitor[gap.competitors[0]] = seen + 1
        kept.append(gap)
        if len(kept) >= limit:
            break
    for gap in kept:
        gap.covered_at = _covered(gap.tokens, site_pages) or ""
    return kept
