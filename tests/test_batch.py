"""Many-pages mode: free plan, estimate, spending cap, cross-page summary."""

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import faqgen
from faqgen_lib.batch import (
    PlanRow,
    build_plan,
    estimate_batch,
    pages_for_plan,
    read_plan,
    write_plan,
)
from test_faqgen import FIXTURE

BLOG_A = """<html><head><title>Walk-In Tub Cost Guide | Client Co</title></head><body>
<h1>Walk-In Tub Cost Guide</h1>
<p>A walk-in tub typically costs between $4,000 and $12,000 installed, depending on
the model and your bathroom layout. Installation labour is included in every quote.</p>
</body></html>"""

BLOG_B = """<html><head><title>Walk-In Tub Safety | Client Co</title></head><body>
<h1>Walk-In Tub Safety</h1>
<p>Low-entry thresholds and built-in seating make bathing safer for people with
limited mobility, and grab bars add support getting in and out of the tub.</p>
</body></html>"""

SITE = {
    "https://client.com/robots.txt": "",
    "https://client.com/sitemap.xml": """<urlset>
      <url><loc>https://client.com/blog/</loc></url>
      <url><loc>https://client.com/blog/walk-in-tub-cost</loc></url>
      <url><loc>https://client.com/blog/walk-in-tub-safety</loc></url>
      <url><loc>https://client.com/fr/blog/baignoire</loc></url>
      <url><loc>https://client.com/blog/category/health</loc></url>
      <url><loc>https://client.com/blog/page/2</loc></url>
      <url><loc>https://client.com/about</loc></url></urlset>""",
    "https://client.com/blog/walk-in-tub-cost": BLOG_A,
    "https://client.com/blog/walk-in-tub-safety": BLOG_B,
}


def fetch(url, **_):
    return SITE.get(url)


def test_plan_lists_a_section_in_the_site_language_only():
    urls = pages_for_plan("https://client.com", "/blog/", fetch=fetch)
    assert urls == ["https://client.com/blog/walk-in-tub-cost",
                    "https://client.com/blog/walk-in-tub-safety"]


def test_plan_suggests_a_query_per_page_and_round_trips(tmp_path):
    rows = build_plan(["https://client.com/blog/walk-in-tub-cost",
                       "https://client.com/blog/missing"], fetch=fetch)
    assert rows[0].queries == ["walk-in tub cost guide"]
    assert rows[1].notes.startswith("could not fetch")
    path = write_plan(rows, tmp_path / "batch-plan.csv")
    back = read_plan(path)
    assert [r.url for r in back] == [r.url for r in rows]
    assert back[0].queries == ["walk-in tub cost guide"]


def test_read_plan_matches_headers_not_positions(tmp_path):
    path = tmp_path / "edited.csv"
    path.write_text(
        "Notes,Prompts,URL,Queries\n"
        ",is it worth it; how long does it last,https://client.com/a,a; b; c; d; e; f\n",
        encoding="utf-8",
    )
    [row] = read_plan(path)
    assert row.queries == ["a", "b", "c", "d", "e"]  # max 5
    assert row.prompts == ["is it worth it", "how long does it last"]


def test_estimate_pays_once_for_shared_queries():
    est = estimate_batch([
        PlanRow(url="u1", queries=["walk in tub", "tub cost"]),
        PlanRow(url="u2", queries=["Walk In Tub"]),
        PlanRow(url="u3", queries=[]),
    ])
    assert (est.total_queries, est.unique_queries, est.pages_without_query) == (3, 2, 1)
    assert est.cost == 0.008
    assert "1 shared between pages" in est.describe()


class FakeClient:
    calls = []

    def __init__(self, transport):
        pass

    def fetch_serp(self, keyword, **_):
        FakeClient.calls.append(keyword)
        raw = copy.deepcopy(FIXTURE)
        raw["cost"] = 0.004
        return raw


class FakeTransport:
    is_sandbox = False

    def describe(self):
        return "fake"


def _run(tmp_path, monkeypatch, rows, extra=()):
    FakeClient.calls = []
    monkeypatch.setattr(faqgen, "DataForSEOClient", FakeClient)
    monkeypatch.setattr(faqgen, "resolve_transport", lambda sandbox=False: FakeTransport())
    monkeypatch.setattr(faqgen, "fetch_html", fetch)
    plan = write_plan(rows, tmp_path / "batch-plan.csv")
    argv = ["--batch", str(plan), "--no-input", "--no-concepts", "--faq-url", "none",
            *extra]
    assert faqgen.main(argv) == 0
    return (tmp_path / "batch-summary.md").read_text(encoding="utf-8")


def test_batch_runs_each_query_once_and_links_across_pages(tmp_path, monkeypatch):
    rows = [
        PlanRow(url="https://client.com/blog/walk-in-tub-cost", queries=["walk in tub cost"]),
        PlanRow(url="https://client.com/blog/walk-in-tub-safety",
                queries=["walk in tub cost"], prompts=["are walk-in tubs safe for seniors"]),
    ]
    summary = _run(tmp_path, monkeypatch, rows)
    assert FakeClient.calls == ["walk in tub cost"]          # shared → paid once
    assert "$0.0040 charged" in summary
    assert summary.index("## What to do") < summary.index("### Per page")
    # Medicare is a gap on both pages: listed once, with one suggested home.
    assert summary.count("Does Medicare pay for a walk-in tub?") == 1
    # The cost question is answered on the cost post, so the safety post links.
    safety = (tmp_path / "blog-walk-in-tub-safety" / "faq-draft.md").read_text()
    assert "the full answer stays on https://client.com/blog/walk-in-tub-cost" in safety
    assert (tmp_path / "blog-walk-in-tub-cost" / "finish-in-claude.md").exists()
    assert not list(tmp_path.rglob("*.jsonld"))


def test_batch_stops_at_the_spending_cap(tmp_path, monkeypatch):
    rows = [
        PlanRow(url="https://client.com/blog/walk-in-tub-cost", queries=["q one"]),
        PlanRow(url="https://client.com/blog/walk-in-tub-safety", queries=["q two"]),
    ]
    summary = _run(tmp_path, monkeypatch, rows, extra=["--budget", "0.005"])
    assert FakeClient.calls == ["q one"]
    assert "- https://client.com/blog/walk-in-tub-safety — spending cap of $0.005 reached" in summary
    assert "### Skipped" in summary


def test_plan_flags_headline_length_queries():
    site = {"https://client.com/x": "<html><head><title>4 Effective Ways to Choose "
            "a Walk-In Tub With or Without a Contractor</title></head><body><p>"
            + "Plenty of readable text on this page to count as content here. " * 3
            + "</p></body></html>"}
    [row] = build_plan(["https://client.com/x"], fetch=site.get)
    assert "shorten it" in row.notes


# ── Resilience (live batch run, 2026-10-22) ──────────────────────────────────

class FlakyClient(FakeClient):
    """Fails 'bad query' every time; everything else works."""

    def fetch_serp(self, keyword, **kw):
        FakeClient.calls.append(keyword)
        raw = copy.deepcopy(FIXTURE)
        raw["cost"] = 0.004
        if keyword == "bad query":
            raw["tasks"][0]["status_code"] = 40101
            raw["tasks"][0]["status_message"] = "Internal SE Server Error."
        return raw


def test_one_failed_lookup_costs_the_query_not_the_page(tmp_path, monkeypatch):
    FakeClient.calls = []
    monkeypatch.setattr(faqgen, "DataForSEOClient", FlakyClient)
    monkeypatch.setattr(faqgen, "resolve_transport", lambda sandbox=False: FakeTransport())
    monkeypatch.setattr(faqgen, "fetch_html", fetch)
    plan = write_plan([PlanRow(url="https://client.com/blog/walk-in-tub-cost",
                               queries=["bad query", "good query"])],
                      tmp_path / "batch-plan.csv")
    faqgen.main(["--batch", str(plan), "--no-input", "--no-concepts",
                 "--faq-url", "none"])
    assert FakeClient.calls == ["bad query", "bad query", "good query"]  # one retry
    summary = (tmp_path / "batch-summary.md").read_text()
    assert "1 drafted, 0 skipped" in summary
    assert "- bad query — DataForSEO task error 40101" in summary
    assert "$0.0120 charged" in summary       # failed attempts are still counted


def test_partial_results_are_used_not_rejected():
    from faqgen_lib.serp import parse_serp
    raw = copy.deepcopy(FIXTURE)
    raw["tasks"][0]["status_code"] = 40106
    assert len(parse_serp(raw, "kw").paa) == 2


def test_jsonld_question_entities_are_decoded():
    from faqgen_lib.page import parse_html
    page = parse_html("https://x.com/p", """<body><script type="application/ld+json">
      {"@type":"FAQPage","mainEntity":[{"@type":"Question",
       "name":"Does the Client Co&#x2122; seat fold away?",
       "acceptedAnswer":{"@type":"Answer","text":"Yes &amp; it lifts out."}}]}
      </script></body>""")
    assert page.qa_pairs == [("Does the Client Co™ seat fold away?",
                              "Yes & it lifts out.")]
