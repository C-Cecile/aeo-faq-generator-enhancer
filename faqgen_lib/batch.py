"""FAQs for many pages at once: plan for free, see the cost, then run.

The plan is a CSV the consultant edits (in Sheets or Excel) before anything is
spent: one row per page, with suggested target queries and room for exact
prompts. The estimate counts each distinct query once, because a query shared
by several pages is looked up once and paid once.
"""

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from .drafting import FAQDraft, tokenize
from .page import PageContent, fetch_html, parse_html
from .serp import EST_COST_PER_KEYWORD
from .site import language_prefix, normalize_url, sitemap_urls, topical_relevance

Fetcher = Callable[[str], Optional[str]]

PLAN_COLUMNS = ["url", "title", "queries", "prompts", "notes"]
# Listing pages, not content: archives, taxonomies, pagination.
_NOT_CONTENT = re.compile(r"/(?:category|categories|tag|tags|author|page|feed)(?:/|$)", re.I)
# Google lookups per page. A third of live lookups returned no People Also Ask
# box (2026-10-23), so 3 left runs short of the default 8 questions.
MAX_QUERIES = 5
# A suggested query longer than this is a headline, not a search.
_MAX_QUERY_WORDS = 6
# Margin over the estimate before a batch stops itself. Live charges have
# come in just under the per-query estimate, so 1.5x only trips on surprises.
DEFAULT_BUDGET_FACTOR = 1.5


@dataclass
class PlanRow:
    url: str
    title: str = ""
    queries: List[str] = field(default_factory=list)
    prompts: List[str] = field(default_factory=list)
    notes: str = ""


def _split(cell: str) -> List[str]:
    return [part.strip() for part in (cell or "").split(";") if part.strip()]


def pages_for_plan(
    source: str, section: str = "", max_pages: int = 30, fetch: Fetcher = fetch_html,
) -> List[str]:
    """Page URLs from a text file (one per line) or a site's sitemap.

    ``section`` keeps sitemap URLs whose path starts with it, e.g. "/blog/".
    """
    path = Path(source)
    if not source.startswith("http") and path.exists():
        urls = [
            line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("http")
        ]
    else:
        language = language_prefix(source)
        urls = [
            u for u in sitemap_urls(source, fetch)
            if urlparse(u).path.startswith(section or "/")
            and not _NOT_CONTENT.search(urlparse(u).path)
            and language_prefix(u) == language
            and normalize_url(u) != normalize_url(f"{source.rstrip('/')}{section}")
        ]
    seen, unique = set(), []
    for url in urls:
        if normalize_url(url) not in seen:
            seen.add(normalize_url(url))
            unique.append(url)
    return unique[:max_pages]


def build_plan(urls: Sequence[str], fetch: Fetcher = fetch_html,
               citations=None) -> List[PlanRow]:
    """Fetch each page (free) and suggest one target query from its title/H1.

    With AI citation data, each row's notes also list up to two prompts on the
    page's topic where assistants rarely cite the site, to copy into the
    prompts column if they fit. They are never added automatically.
    """
    rows = []
    for url in urls:
        html = fetch(url)
        if not html:
            rows.append(PlanRow(url=url, notes="could not fetch — check or remove"))
            continue
        page = parse_html(url, html)
        query = (page.keyword_suggestions() or [""])[0].rstrip("?").strip()
        notes = []
        if not page.paragraphs:
            notes.append("little readable text — JavaScript page?")
        if len(query.split()) > _MAX_QUERY_WORDS:
            notes.append("query is the full headline: shorten it to how people search")
        if citations:
            weak = citations.weak_prompts(url, [query, page.title, page.h1], limit=2)
            if weak:
                notes.append("prompts AI rarely cites the site for (copy into prompts "
                             "if they fit): " + " | ".join(p for p, _, _ in weak))
        rows.append(PlanRow(
            url=url, title=page.h1 or page.title,
            queries=[query] if query else [], notes="; ".join(notes),
        ))
    return rows


def write_plan(rows: Sequence[PlanRow], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=PLAN_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "url": row.url, "title": row.title,
                "queries": "; ".join(row.queries), "prompts": "; ".join(row.prompts),
                "notes": row.notes,
            })
    return path


def read_plan(path: Path) -> List[PlanRow]:
    """Matched by header name, so reordered or extra columns are fine."""
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        headers = {h.strip().lower(): h for h in reader.fieldnames or []}
        if "url" not in headers:
            raise ValueError(f"{path} has no 'url' column.")

        def cell(record: Dict[str, str], name: str) -> str:
            return (record.get(headers.get(name, ""), "") or "").strip()

        rows = []
        for record in reader:
            url = cell(record, "url")
            if url.startswith("http"):
                rows.append(PlanRow(
                    url=url, title=cell(record, "title"),
                    queries=_split(cell(record, "queries"))[:MAX_QUERIES],
                    prompts=_split(cell(record, "prompts")),
                    notes=cell(record, "notes"),
                ))
    return rows


@dataclass
class Estimate:
    pages: int
    pages_without_query: int
    total_queries: int
    unique_queries: int
    cost: float

    def describe(self) -> str:
        shared = self.total_queries - self.unique_queries
        lines = [
            f"{self.pages} pages, {self.unique_queries} distinct queries "
            f"to look up ({shared} shared between pages, paid once).",
            f"Estimated DataForSEO cost: ~${self.cost:.2f} "
            f"(~${EST_COST_PER_KEYWORD:.3f} per query). Reading pages is free.",
        ]
        if self.pages_without_query:
            lines.append(
                f"{self.pages_without_query} pages have no query and will be "
                "skipped unless they have prompts."
            )
        return "\n".join(lines)


def estimate_batch(rows: Sequence[PlanRow]) -> Estimate:
    queries = [q.lower() for row in rows for q in row.queries]
    unique = len(set(queries))
    return Estimate(
        pages=len(rows),
        pages_without_query=sum(1 for r in rows if not r.queries),
        total_queries=len(queries),
        unique_queries=unique,
        cost=round(unique * EST_COST_PER_KEYWORD, 4),
    )


def page_slug(url: str) -> str:
    path = urlparse(url).path.strip("/") or "home"
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")[:80] or "page"


@dataclass
class PageOutcome:
    row: PlanRow
    folder: str = ""
    drafts: List[FAQDraft] = field(default_factory=list)
    page: Optional[PageContent] = None
    skipped: str = ""


def _status(d: FAQDraft) -> str:
    return d.status or ("gap" if d.page_gap else "answered")


def render_batch_summary(
    outcomes: Sequence[PageOutcome], estimate: Estimate, actual_cost: float,
    budget: float, failed: Optional[Dict[str, str]] = None,
) -> str:
    done = [o for o in outcomes if not o.skipped]
    skipped = [o for o in outcomes if o.skipped]
    lines = [
        "# FAQ batch summary",
        "",
        f"- **Pages:** {len(done)} drafted, {len(skipped)} skipped",
        f"- **DataForSEO cost:** ${actual_cost:.4f} charged "
        f"(estimated ~${estimate.cost:.3f}, cap ${budget:.3f})",
        "",
        "## What to do",
        "",
    ]

    # Content to write, once per question across the batch. A question that
    # no page can answer gets one suggested home: the page most centred on it.
    gaps: Dict[str, Dict] = {}
    for o in done:
        for d in o.drafts:
            if _status(d) != "gap":
                continue
            key = " ".join(sorted(tokenize(d.question.question)))
            entry = gaps.setdefault(
                key, {"question": d.question.question, "pages": []}
            )
            entry["pages"].append(o)
    if gaps:
        lines += [
            "### Content to write",
            "",
            "Nothing on the site answers these. Each is listed once, with the",
            "page best placed to carry the full answer; the other pages that",
            "surfaced it should link there once it exists.",
            "",
        ]
        for entry in gaps.values():
            home = max(
                entry["pages"],
                key=lambda o: topical_relevance(entry["question"], o.page, False)
                if o.page else 0.0,
            )
            others = list(dict.fromkeys(
                o.row.url for o in entry["pages"] if o.row.url != home.row.url
            ))
            also = f" · also asked for: {', '.join(others)}" if others else ""
            lines.append(f"- {entry['question']} → **{home.row.url}**{also}")
        lines.append("")

    checks = [(o.row.url, d.question.question) for o in done for d in o.drafts
              if _status(d) == "check"]
    if checks:
        lines += [
            "### Needs your decision",
            "",
            "Word matching can't tell whether these are answered; the closest",
            "passage is under each question in that page's draft.",
            "",
        ]
        lines += [f"- {q} — {url}" for url, q in checks]
        lines.append("")

    trims = [
        (o.row.url, d.question.question, url)
        for o in done for d in o.drafts for url in d.trim_elsewhere
    ]
    if trims:
        lines += [
            "### Changes for other pages",
            "",
            "Each of these pages publishes a question whose full answer now lives",
            "on another page. Remove its full answer, keep at most one sentence",
            "linking to that page, and remove the question from its FAQPage markup",
            "if it has any.",
            "",
        ]
        lines += [f"- {url} — “{q}” → link to {home}" for home, q, url in trims]
        lines.append("")

    lines += [
        "### Per page",
        "",
        "| Page | Candidates: here | Candidates: moved here | Candidates: short + link | Needs your decision | Content to write | Draft |",
        "|---|---|---|---|---|---|---|",
    ]
    for o in done:
        c = {s: sum(1 for d in o.drafts if _status(d) == s)
             for s in ("answered", "home_here", "linked", "check", "gap")}
        lines.append(
            f"| {o.row.url} | {c['answered']} | {c['home_here']} | {c['linked']} "
            f"| {c['check']} | {c['gap']} | `{o.folder}/faq-draft.md` |"
        )
    lines.append("")

    if failed:
        lines += [
            "### Lookups that failed",
            "",
            "DataForSEO errored twice on these; the pages used their other",
            "queries and prompts. Re-run them later if they matter.",
            "",
        ]
        lines += [f"- {kw} — {err}" for kw, err in failed.items()]
        lines.append("")

    if skipped:
        lines += ["### Skipped", ""]
        lines += [f"- {o.row.url} — {o.skipped}" for o in skipped]
        lines.append("")
    return "\n".join(lines)
