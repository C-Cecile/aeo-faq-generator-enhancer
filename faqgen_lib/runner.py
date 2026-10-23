"""Run the FAQ pipeline for one page or a batch, without any CLI.

The command-line tool and the seo-audit pipeline step both call this, so the
two can't drift. Nothing here prompts or reads arguments: callers settle the
options, the spending cap and the DataForSEO client first.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from . import FaqgenError
from .batch import (
    PageOutcome,
    PlanRow,
    estimate_batch,
    page_slug,
    render_batch_summary,
)
from .concepts import competitor_pages, find_concept_gaps
from .drafting import draft_answer, prioritize_questions, split_off_topic
from .intent import determine_intent
from .page import fetch_html, parse_html
from .render import write_outputs
from .serp import (
    EST_COST_PER_KEYWORD,
    LOCATION_CODES,
    SerpData,
    citation_provenance,
    parse_serp,
    prompt_questions,
    question_shaped_related,
)
from .site import load_site_pages, place_answer

Log = Callable[[str], None]


@dataclass
class RunOptions:
    location: str = "us"
    language: str = "en"
    paa_depth: int = 2
    max_questions: int = 8
    concepts: bool = True
    site_check: bool = True
    # None = detect the general FAQ page; [] = the site has none.
    faq_urls: Optional[List[str]] = None
    extra_site_pages: List[str] = field(default_factory=list)
    user_agent: str = ""
    sandbox: bool = False
    # AI citation data (citations.CitationData): decides which page owns each
    # question when it has enough evidence. None = title/H1/URL only.
    citations: Optional[object] = None
    log: Log = print


class SerpCache:
    """One paid lookup per query, however many pages ask for it."""

    def __init__(self, client, options: RunOptions):
        self.client, self.options = client, options
        self.serps, self.raw = {}, {}
        self.failed = {}   # keyword -> error, after one retry
        self.cost = 0.0

    def _lookup(self, keyword: str):
        raw = self.client.fetch_serp(
            keyword,
            location_code=LOCATION_CODES[self.options.location],
            language_code=self.options.language,
            paa_depth=self.options.paa_depth,
        )
        self.cost += float(raw.get("cost") or 0.0)
        return raw, parse_serp(raw, keyword)

    def get(self, keyword: str):
        """The query's SERP; an empty one if it fails twice, so one bad
        lookup costs that query, not the whole page."""
        log = self.options.log
        key = keyword.strip().lower()
        if key not in self.serps:
            try:
                try:
                    raw, serp = self._lookup(keyword)
                except FaqgenError as first:
                    log(f"  {keyword!r}: {str(first).splitlines()[0]}, retrying once")
                    raw, serp = self._lookup(keyword)
            except FaqgenError as exc:
                self.failed[keyword] = str(exc).splitlines()[0]
                log(f"  {keyword!r}: failed again, continuing without it")
                self.serps[key] = SerpData(keyword=keyword)
                return self.serps[key]
            self.serps[key], self.raw[keyword] = serp, raw
            log(f"  {keyword!r}: {len(serp.paa)} PAA questions, "
                f"{len(serp.related_searches)} related searches, "
                f"{len(serp.ai_overview_refs)} AI Overview citations "
                f"(charged ${serp.cost:.4f})")
        return self.serps[key]


def build_page_faq(page, keywords, prompts, serps: SerpCache, options: RunOptions,
                   site_pages, fetch: Optional[Callable[[str], Optional[str]]] = None) -> dict:
    """Questions, placement and concept gaps for one page. Returns a dict."""
    candidates, organic, provenance = [], [], []
    for kw in keywords:
        serp = serps.get(kw)
        candidates.extend(serp.paa)
        candidates.extend(question_shaped_related(serp))
        organic.extend(serp.organic)
        if not provenance:
            provenance = citation_provenance(serp)

    # The user's prompts go first so they win any merge with a PAA duplicate.
    candidates = prompt_questions(prompts) + candidates
    if not candidates:
        raise FaqgenError(
            "Google returned no People Also Ask questions for these keywords. "
            "Try broader or more question-prone keywords (how/what/cost/…)."
        )

    candidates, off_topic = split_off_topic(candidates, page, keywords)
    intent, rationale = determine_intent(keywords, organic)
    # Rank more than we keep: questions the page already answers drop out.
    drafts = prioritize_questions(candidates, page, options.max_questions * 3)
    drafts = [draft_answer(d, page) for d in drafts]
    if site_pages is not None:
        drafts = [place_answer(d, page, site_pages, options.citations) for d in drafts]
    for d in drafts:
        # Word matching can't tell a conversational prompt answered in other
        # words ("who installs it... how long" vs "two days... certified
        # fitter") from one that isn't answered. Never call the user's own
        # prompt unanswered when a related passage exists; ask for a decision.
        if d.question.from_prompt and d.status == "gap" and d.near_miss_excerpt:
            d.status = "check"
    existing = [d for d in drafts if d.status == "exists_here"]
    # Every prompt the user named is kept; Google's questions fill the rest.
    keep = max(options.max_questions, len(prompts))
    drafts = [d for d in drafts if d.status != "exists_here"][:keep]

    concept_gaps = []
    if options.concepts and not options.sandbox:
        options.log("Comparing the page with the top-ranking competitors…")
        competitors = competitor_pages(
            organic, page.url,
            fetch=fetch or (lambda u: fetch_html(u, user_agent=options.user_agent)),
        )
        concept_gaps = find_concept_gaps(
            page, competitors, keywords, [sp.page for sp in site_pages or []],
        )
    return {
        "drafts": drafts, "existing": existing, "intent": intent,
        "rationale": rationale, "provenance": provenance,
        "concept_gaps": concept_gaps, "off_topic": off_topic,
    }


def summary_counts(drafts) -> dict:
    return {s: sum(1 for d in drafts if d.status == s)
            for s in ("answered", "home_here", "linked", "check", "gap")}


@dataclass
class BatchResult:
    outcomes: List[PageOutcome]
    cost: float
    estimated: float
    budget: float
    failed: Dict[str, str]
    summary_path: Path

    @property
    def drafted(self) -> int:
        return sum(1 for o in self.outcomes if not o.skipped)


def run_batch(
    rows: Sequence[PlanRow], client, out_dir: Path, budget: float,
    options: RunOptions, fetch: Optional[Callable[[str], Optional[str]]] = None,
) -> BatchResult:
    """Draft every page in the plan, stopping before spend would pass ``budget``.

    Writes one folder per page plus ``batch-summary.md`` into ``out_dir``.
    """
    estimate = estimate_batch(rows)
    serps = SerpCache(client, options)

    # Every page reads the same FAQ hub, sitemap and fellow batch pages; fetch
    # each URL once.
    cache = {}

    def cached(url):
        if url not in cache:
            cache[url] = (fetch or (lambda u: fetch_html(u, user_agent=options.user_agent)))(url)
        return cache[url]

    log = options.log
    batch_urls = [row.url for row in rows]
    outcomes = []
    for n, row in enumerate(rows, 1):
        log(f"\n[{n}/{len(rows)}] {row.url}")
        outcome = PageOutcome(row=row, folder=page_slug(row.url))
        outcomes.append(outcome)
        if not row.queries and not row.prompts:
            outcome.skipped = "no queries or prompts in the plan"
            continue
        new = {q.lower() for q in row.queries} - set(serps.serps)
        if serps.cost + len(new) * EST_COST_PER_KEYWORD > budget:
            outcome.skipped = f"spending cap of ${budget:.3f} reached"
            log(f"  skipped: {outcome.skipped}")
            continue
        site_notes = None
        try:
            html = cached(row.url)
            if not html:
                raise FaqgenError("could not fetch the page")
            page = parse_html(row.url, html)
            outcome.page = page
            for kw in row.queries:
                serps.get(kw)
            site_pages = None
            if options.site_check:
                site_pages, site_notes = load_site_pages(
                    page, row.queries, faq_urls=options.faq_urls,
                    extra_urls=[*options.extra_site_pages, *batch_urls],
                    max_related=6, fetch=cached,
                )
            result = build_page_faq(
                page, row.queries, row.prompts, serps, options, site_pages, fetch=cached,
            )
        except FaqgenError as exc:
            outcome.skipped = str(exc).splitlines()[0]
            log(f"  skipped: {outcome.skipped}")
            continue
        outcome.drafts = result["drafts"]
        page_dir = Path(out_dir) / outcome.folder
        page_dir.mkdir(parents=True, exist_ok=True)
        write_outputs(
            page_dir, page, result["drafts"] + result["existing"],
            result["intent"], result["rationale"], serps.cost,
            {kw: serps.raw.get(kw) for kw in row.queries},
            result["provenance"], options.sandbox,
            site_notes, result["concept_gaps"], result["off_topic"],
            meta={"market": options.location, "language": options.language,
                  "queries": row.queries, "prompts": row.prompts,
                  "citations": options.citations.describe() if options.citations else ""},
        )

    summary = Path(out_dir) / "batch-summary.md"
    summary.write_text(
        render_batch_summary(outcomes, estimate, serps.cost, budget, serps.failed),
        encoding="utf-8",
    )
    return BatchResult(
        outcomes=outcomes, cost=serps.cost, estimated=estimate.cost,
        budget=budget, failed=serps.failed, summary_path=summary,
    )
