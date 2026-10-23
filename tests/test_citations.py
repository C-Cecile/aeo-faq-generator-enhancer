"""AI citation data: reading exports and using them to choose each question's home.

Synthetic data only, shaped like a Profound export and a generic CSV.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import faqgen
from faqgen_lib import citations as C
from faqgen_lib.drafting import FAQDraft, draft_answer
from faqgen_lib.page import parse_html
from faqgen_lib.render import render_markdown
from faqgen_lib.serp import PAAQuestion
from faqgen_lib.site import SitePage, place_answer

HOME = "https://www.client.com/"
PROGRAM = "https://www.client.com/installation"
Q = "What is walk-in tub installation?"


def _profound(tmp_path, home=12, program=1, branded_program=40):
    rows = []
    for i in range(home):
        rows.append({"type": "citation", "url": HOME + "#:~:text=x", "prompt": f"How long does walk-in tub installation take? v{i}",
                     "tags": "Unbranded", "topic": "Walk-in tub installation", "date": "2026-10-05",
                     "text": "Walk-in tubs, installed by certified fitters."})
    for i in range(program):
        rows.append({"type": "citation", "url": PROGRAM, "prompt": "How long does walk-in tub installation take? v0",
                     "tags": "Unbranded", "topic": "Walk-in tub installation", "date": "2026-10-21"})
    for i in range(branded_program):      # branded: must not count
        rows.append({"type": "citation", "url": PROGRAM, "prompt": "Is Client's walk-in tub installation good?",
                     "tags": "Branded", "topic": "Client installation"})
    for i in range(6):                    # rival answers a prompt the client misses
        rows.append({"type": "citation", "url": "https://rival.com/x", "prompt": "Is a certified walk-in tub installer better than a handyman?",
                     "tags": "Unbranded", "topic": "Walk-in tub installation"})
    rows.append({"type": "web_search_result", "url": PROGRAM, "prompt": "How long does walk-in tub installation take? v0",
                 "tags": "Unbranded"})
    (tmp_path / "citations_data.json").write_text(json.dumps({"filters": {}, "data": rows}))
    (tmp_path / "query_fanouts_by_prompt.json").write_text(json.dumps({"data": [
        {"prompt": "Is a certified walk-in tub installer better than a handyman?",
         "fanout_query": "certified walk in tub installer vs handyman", "fanout_share": 0.4},
        {"prompt": "Is a certified walk-in tub installer better than a handyman?",
         "fanout_query": "walk in tub diy install", "fanout_share": 0.1},
    ]}))
    return C.load(C.discover([tmp_path]))


def test_profound_export_is_read_and_filtered(tmp_path):
    data = _profound(tmp_path)
    assert len(data.citations) == 12 + 1 + 40 + 6          # web_search_result dropped
    assert len(data.fanouts) == 2
    assert {c.branded for c in data.citations} == {True, False}
    assert "2026-10-05 to 2026-10-21" in data.describe()


def test_csv_from_another_tool_with_other_column_names(tmp_path):
    (tmp_path / "ai-visibility-citations.csv").write_text(
        "Cited URL,Query,Prompt Type,Engine\n"
        "https://www.client.com/,best walk-in tub installer,non-branded,ChatGPT\n"
        "https://www.client.com/,client reviews,branded,Gemini\n", encoding="utf-8")
    data = C.load(C.discover(search_dirs=[tmp_path]))
    assert [(c.prompt, c.branded, c.platform) for c in data.citations] == [
        ("best walk-in tub installer", False, "ChatGPT"), ("client reviews", True, "Gemini")]


def test_discover_prefers_explicit_paths_then_folder_and_name_hints(tmp_path):
    (tmp_path / "citations").mkdir()
    (tmp_path / "citations" / "export.csv").write_text("url,prompt\n")
    (tmp_path / "query_fanouts.json").write_text("{}")
    (tmp_path / "unrelated.json").write_text("{}")
    found = {p.name for p in C.discover(search_dirs=[tmp_path])}
    assert found == {"export.csv", "query_fanouts.json"}
    assert C.discover([tmp_path / "unrelated.json"]) == [tmp_path / "unrelated.json"]


def test_unbranded_citations_decide_the_page_and_branded_ones_dont(tmp_path):
    data = _profound(tmp_path)
    counts = data.cited_pages(Q, HOME)
    assert counts == {"client.com/": 12, "client.com/installation": 1}   # 40 branded ignored


def test_weak_prompts_and_fanouts(tmp_path):
    data = _profound(tmp_path)
    weak = data.weak_prompts(HOME, ["walk-in tub installer"])
    assert weak[0] == ("Is a certified walk-in tub installer better than a handyman?", 0.0,
                       "Walk-in tub installation")
    assert data.fanout_queries([weak[0][0]]) == [
        "certified walk in tub installer vs handyman", "walk in tub diy install"]


def _pages():
    home = parse_html(HOME, """<html><head><title>Client | Walk-in tubs</title></head><body>
      <h1>Walk-in tubs that last</h1>
      <p>Our walk-in tub installation takes two days with a certified fitter, plus a
      check-in visit after the first month.</p>
      <p>Book a free consult to see whether a walk-in tub fits you and your bathroom.</p>
      <p>Fitters cover the whole state, so you can book a visit at a time that suits.</p>
      </body></html>""")
    program = parse_html(PROGRAM, """<html><head><title>Walk-In Tub Installation | Client</title></head>
      <body><h1>Walk-in tub installation</h1>
      <p>Walk-in tub installation takes two days with a certified fitter, with a check-in
      visit after the first month and a plan built around your bathroom.</p></body></html>""")
    return home, program


def _draft(page, question):
    return draft_answer(FAQDraft(question=PAAQuestion(question=question, rank=1), score=0), page)


def test_cited_homepage_keeps_the_answer_over_a_better_titled_page(tmp_path):
    data = _profound(tmp_path)
    home, program = _pages()
    question = "How many days does walk-in tub installation take?"
    without = place_answer(_draft(home, question), home, [SitePage(page=program)])
    assert without.status == "linked" and without.link_url == PROGRAM      # title wins
    with_data = place_answer(_draft(home, question), home, [SitePage(page=program)], data)
    assert with_data.status == "answered"
    # /program only has the material as body text: it keeps it, nothing to hand over.
    assert with_data.trim_elsewhere == []
    assert with_data.home_evidence.startswith("AI assistants cite it 12 of 13 times")


def test_too_little_evidence_falls_back_to_title_and_says_so(tmp_path):
    data = _profound(tmp_path, home=2, program=1)
    home, program = _pages()
    d = place_answer(_draft(home, "How many days does walk-in tub installation take?"), home,
                     [SitePage(page=program)], data)
    assert d.status == "linked"
    assert d.home_evidence.startswith("its title, heading and URL")


def test_draft_names_the_data_and_the_reason(tmp_path):
    data = _profound(tmp_path)
    home, program = _pages()
    d = place_answer(_draft(home, "How many days does walk-in tub installation take?"), home,
                     [SitePage(page=program)], data)
    md = render_markdown(home, [d], "commercial", "x", 0.004, site_notes=["checked"],
                         meta={"citations": data.describe()})
    assert "| AI citation data | 59 citations and 2 fan-out searches" in md
    assert "- **Why this page carries the full answer:** AI assistants cite it 12 of 13" in md


def test_cli_finds_and_uses_citation_data_without_asking(tmp_path, monkeypatch):
    _profound(tmp_path)
    monkeypatch.chdir(tmp_path)
    args = faqgen.build_parser().parse_args(["https://x.com", "--no-input"])
    data = faqgen.settle_citations(args, interactive=False)
    assert data and len(data.citations) == 59
    args = faqgen.build_parser().parse_args(["https://x.com", "--no-citations"])
    assert faqgen.settle_citations(args, interactive=False) is None
