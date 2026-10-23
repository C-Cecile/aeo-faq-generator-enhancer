"""Tests for the standalone FAQ generator (fixture-based, no network)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faqgen_lib.drafting import (
    ANSWER_OVERLAP_THRESHOLD,
    draft_answer,
    prioritize_questions,
)
from faqgen_lib.intent import determine_intent
from faqgen_lib.page import PageContent, parse_html
from faqgen_lib.render import CANDIDATE, render_markdown
from faqgen_lib.serp import (
    PAAQuestion,
    OrganicResult,
    parse_serp,
    question_shaped_related,
)

FIXTURE = {
    "status_code": 20000,
    "cost": 0.008,
    "tasks": [{
        "status_code": 20000,
        "result": [{
            "items": [
                {
                    "type": "organic",
                    "rank_group": 1,
                    "url": "https://example.com/blog/walk-in-tub-cost-guide",
                    "title": "Walk-In Tub Cost Guide: What to Expect",
                    "description": "A complete guide to walk-in tub pricing.",
                },
                {
                    "type": "people_also_ask",
                    "items": [
                        {
                            "type": "people_also_ask_element",
                            "title": "How much does a walk-in tub cost installed?",
                            "expanded_element": [{
                                "type": "people_also_ask_expanded_element",
                                "description": "Installed costs range from $5,000 to $15,000.",
                                "url": "https://competitor.com/tub-costs",
                                "domain": "competitor.com",
                                "title": "Tub Costs Explained",
                            }],
                        },
                        {
                            "type": "people_also_ask_element",
                            "title": "Does Medicare pay for a walk-in tub?",
                            "expanded_element": [{
                                "type": "people_also_ask_expanded_element",
                                "description": "Medicare does not typically cover walk-in tubs.",
                                "url": "https://competitor.com/medicare-tubs",
                                "domain": "competitor.com",
                                "title": "Medicare and Walk-In Tubs",
                            }],
                        },
                    ],
                },
                {
                    "type": "related_searches",
                    "items": [
                        {"title": "walk in tub prices seniors"},
                        {"title": "how long does a walk in tub install take"},
                    ],
                },
            ],
        }],
    }],
}


def _page():
    page = PageContent(
        url="https://client.com/walk-in-tubs",
        title="Walk-In Tubs | Client Co",
        h1="Walk-In Tubs",
    )
    page.paragraphs = [
        (
            "A walk-in tub typically costs between $4,000 and $12,000 installed, "
            "depending on the model and your bathroom layout. Most installations "
            "are completed within two days. Financing options are available.",
            "Pricing",
        ),
        (
            "Our tubs feature low-entry thresholds, built-in seating, and "
            "hydrotherapy jets designed for safety and comfort.",
            "Features",
        ),
    ]
    return page


# ── SERP parsing ─────────────────────────────────────────────────────────────

def test_parse_serp_extracts_paa_questions():
    serp = parse_serp(FIXTURE, "walk in tub cost")
    assert [q.question for q in serp.paa] == [
        "How much does a walk-in tub cost installed?",
        "Does Medicare pay for a walk-in tub?",
    ]
    assert serp.paa[0].source_domain == "competitor.com"
    assert serp.paa[0].answer_text.startswith("Installed costs")
    assert serp.paa[1].source_url == "https://competitor.com/medicare-tubs"
    assert serp.cost == 0.008


def test_parse_serp_organic_and_related():
    serp = parse_serp(FIXTURE, "walk in tub cost")
    assert len(serp.organic) == 1
    assert serp.organic[0].rank == 1
    assert serp.related_searches == [
        "walk in tub prices seniors",
        "how long does a walk in tub install take",
    ]


def test_question_shaped_related_filters_non_questions():
    serp = parse_serp(FIXTURE, "walk in tub cost")
    related = question_shaped_related(serp)
    assert [q.question for q in related] == [
        "how long does a walk in tub install take?"
    ]
    assert related[0].from_related


def test_parse_serp_malformed_response_returns_empty():
    serp = parse_serp({"tasks": []}, "kw")
    assert serp.paa == [] and serp.organic == []


# ── Drafting ─────────────────────────────────────────────────────────────────

def test_answer_extracted_when_page_covers_question():
    draft = prioritize_questions(parse_serp(FIXTURE, "walk in tub cost").paa, _page(), 8)[0]
    draft = draft_answer(draft, _page())
    assert not draft.page_gap
    assert "$4,000" in draft.answer
    assert draft.source_heading == "Pricing"


def test_page_gap_flagged_when_no_material():
    q = PAAQuestion(question="Does Medicare pay for a walk-in tub?", rank=2)
    drafts = prioritize_questions([q], _page(), 8)
    draft = draft_answer(drafts[0], _page())
    assert draft.page_gap
    assert draft.answer == ""


def test_duplicate_questions_merge_across_keywords():
    q1 = PAAQuestion(question="How much does a walk-in tub cost installed?",
                     rank=1, keyword="kw one")
    q2 = PAAQuestion(question="how much does a walk-in tub cost installed",
                     rank=3, keyword="kw two")
    drafts = prioritize_questions([q1, q2], _page(), 8)
    assert len(drafts) == 1
    assert sorted(drafts[0].surfaced_by) == ["kw one", "kw two"]


def test_threshold_is_sane():
    assert 0 < ANSWER_OVERLAP_THRESHOLD < 1


def test_same_topic_paragraph_is_not_an_answer():
    """Regression: a paragraph sharing topic words but not answering the question.

    Caught on a live run 2026-09-23 — "What are the drawbacks of a walk-in tub?"
    was answered with a general definition paragraph purely because both
    mentioned walk-in tubs, and that false answer then went into the emitted
    FAQPage schema. The question's distinctive word ("drawbacks") is absent, so
    this must be a gap.
    """
    page = PageContent(url="https://x.com/t", title="Walk-In Tubs", h1="Walk-In Tubs")
    page.paragraphs = [
        ("Walk-in tubs are bathtubs that can be used by people with limited "
         "mobility, the elderly, and those with disabilities. They have a "
         "door in the side wall of the tub.", "About"),
        ("Our walk-in tubs include grab bars and a built-in seat for comfort.",
         "Features"),
    ]
    q = PAAQuestion(question="What are the drawbacks of a walk-in tub?", rank=1)
    draft = draft_answer(prioritize_questions([q], page, 8)[0], page)
    assert draft.page_gap, "same-topic paragraph must not count as an answer"
    assert draft.answer == ""
    # But the closest passage is still reported, for the content brief.
    assert draft.near_miss_excerpt


def test_near_miss_excluded_from_schema():
    page = PageContent(url="https://x.com/t", title="Walk-In Tubs", h1="Walk-In Tubs")
    page.paragraphs = [
        ("Walk-in tubs are bathtubs with a door in the side wall, used by "
         "people with limited mobility.", "About"),
    ]
    q = PAAQuestion(question="Does Medicare cover a walk-in tub?", rank=1)
    draft = draft_answer(prioritize_questions([q], page, 8)[0], page)
    assert draft.status not in CANDIDATE


# ── Rendering ────────────────────────────────────────────────────────────────

def _drafts():
    page = _page()
    drafts = prioritize_questions(parse_serp(FIXTURE, "walk in tub cost").paa, page, 8)
    return [draft_answer(d, page) for d in drafts]


def test_candidates_section_excludes_gaps():
    md = render_markdown(_page(), _drafts(), "informational", "test", 0.008)
    section = md[md.index("## Candidate answers: verify before publishing"):
                 md.index("## Content to write")]
    assert "How much does a walk-in tub cost installed?" in section
    assert "Does Medicare pay for a walk-in tub?" not in section  # page gap
    assert "**Verify** the 1 candidate answers" in md


def test_markdown_contains_gap_flag_and_competitor_source():
    md = render_markdown(_page(), _drafts(), "informational", "test", 0.008)
    assert "## Content to write" in md
    assert "this question's" in md and "## Content to write" in md
    assert "competitor.com" in md
    assert "$0.0080" in md


# ── Intent ───────────────────────────────────────────────────────────────────

def test_intent_informational_for_how_much_keyword():
    organic = [OrganicResult(
        url="https://example.com/blog/guide", title="Guide", description="", rank=1
    )]
    intent, rationale = determine_intent(["how much does a walk in tub cost"], organic)
    assert intent == "informational"
    assert rationale


def test_intent_commercial_for_best_keyword():
    intent, _ = determine_intent(["best walk in tubs"], [])
    assert intent == "commercial"


# ── Page parsing ─────────────────────────────────────────────────────────────

def test_parse_html_extracts_structure():
    html = """
    <html><head><title>Walk-In Tubs | Client</title>
    <meta name="description" content="Safe bathing solutions."></head>
    <body><nav><p>Menu item that is long enough to be a paragraph but in nav</p></nav>
    <h1>Walk-In Tubs</h1>
    <h2>Pricing</h2>
    <p>A walk-in tub typically costs between four and twelve thousand dollars
    installed, depending on the model you choose.</p>
    <p>short</p>
    </body></html>
    """
    page = parse_html("https://client.com/x", html)
    assert page.title == "Walk-In Tubs | Client"
    assert page.h1 == "Walk-In Tubs"
    assert page.meta_description == "Safe bathing solutions."
    assert len(page.paragraphs) == 1
    assert page.paragraphs[0][1] == "Pricing"
    assert page.derived_keyword() == "walk-in tubs"
