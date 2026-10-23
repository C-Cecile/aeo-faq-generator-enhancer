"""Site-wide check, intake and concept gaps (fake site, no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import faqgen
from faqgen_lib.concepts import find_concept_gaps
from faqgen_lib.drafting import FAQDraft, draft_answer
from faqgen_lib.page import parse_html
from faqgen_lib.render import CANDIDATE, render_markdown
from faqgen_lib.serp import PAAQuestion
from faqgen_lib.site import (
    SitePage,
    load_site_pages,
    place_answer,
    rank_related_urls,
    same_question,
    sitemap_urls,
)

TARGET_URL = "https://client.com/walk-in-tubs"

TARGET_HTML = """<html><head><title>Walk-In Tubs | Client Co</title></head><body>
<nav><a href="/faq">FAQ</a><a href="/walk-in-tubs/pricing">Pricing</a></nav>
<h1>Walk-In Tubs</h1>
<h2>Features</h2>
<p>Our walk-in tubs feature low-entry thresholds, built-in seating, and
hydrotherapy jets designed for safety and comfort in the bathroom.</p>
<h2>How long does installation take?</h2>
<p>Most walk-in tub installations are completed within two days by our crew.</p>
</body></html>"""

FAQ_HTML = """<html><head><title>FAQ | Client Co</title></head><body>
<h1>Frequently Asked Questions</h1>
<details><summary>Does Medicare pay for a walk-in tub?</summary>
<p>Medicare does not usually cover walk-in tubs, but some Medicaid waivers can.</p>
</details>
<details><summary>Do you offer financing?</summary>
<p>Yes, financing is available on every model with approved credit.</p></details>
</body></html>"""

PRICING_HTML = """<html><head><title>Walk-In Tub Cost and Pricing | Client Co</title></head>
<body><h1>Walk-In Tub Cost</h1>
<p>A walk-in tub costs between $4,000 and $12,000 installed, depending on the
model and the bathroom layout. Installation labour is included in every quote.</p>
</body></html>"""

SITEMAP = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://client.com/walk-in-tubs</loc></url>
<url><loc>https://client.com/walk-in-tubs/pricing</loc></url>
<url><loc>https://client.com/about-us</loc></url>
<url><loc>https://client.com/faq</loc></url>
</urlset>"""

SITE = {
    "https://client.com/robots.txt": "User-agent: *\nSitemap: https://client.com/sitemap.xml",
    "https://client.com/sitemap.xml": SITEMAP,
    "https://client.com/faq": FAQ_HTML,
    "https://client.com/walk-in-tubs/pricing": PRICING_HTML,
}


def fake_fetch(url):
    return SITE.get(url)


def _target():
    return parse_html(TARGET_URL, TARGET_HTML)


def _placed(question, target=None):
    target = target or _target()
    pages, _ = load_site_pages(target, ["walk in tub"], fetch=fake_fetch)
    draft = draft_answer(
        FAQDraft(question=PAAQuestion(question=question, rank=1), score=0.0), target
    )
    return place_answer(draft, target, pages)


# ── Parsing what the site already publishes ──────────────────────────────────

def test_parse_html_reads_existing_qa_from_details_and_headings():
    faq = parse_html("https://client.com/faq", FAQ_HTML)
    assert [q for q, _ in faq.qa_pairs] == [
        "Does Medicare pay for a walk-in tub?", "Do you offer financing?",
    ]
    assert faq.qa_pairs[0][1].startswith("Medicare does not usually cover")
    target = _target()
    assert target.qa_pairs[0][0] == "How long does installation take?"


def test_parse_html_reads_faqpage_jsonld():
    html = """<html><body><script type="application/ld+json">
    {"@context":"https://schema.org","@type":"FAQPage","mainEntity":[
      {"@type":"Question","name":"Is it safe?",
       "acceptedAnswer":{"@type":"Answer","text":"<p>Yes, it is.</p>"}}]}
    </script><p>x</p></body></html>"""
    page = parse_html("https://client.com/x", html)
    assert page.qa_pairs == [("Is it safe?", "Yes, it is.")]


def test_parse_html_keeps_nav_links_absolute():
    assert ("https://client.com/faq", "FAQ") in _target().links


# ── Discovery ────────────────────────────────────────────────────────────────

def test_sitemap_urls_follow_robots_and_child_sitemaps():
    site = {
        "https://client.com/robots.txt": "Sitemap: https://client.com/index.xml",
        "https://client.com/index.xml": (
            "<sitemapindex><sitemap><loc>https://client.com/pages.xml</loc>"
            "</sitemap></sitemapindex>"
        ),
        "https://client.com/pages.xml": (
            "<urlset><url><loc>https://client.com/a</loc></url>"
            "<url><loc>https://other.com/b</loc></url>"
            "<url><loc>https://client.com/photo.jpg</loc></url></urlset>"
        ),
    }
    assert sitemap_urls(TARGET_URL, site.get) == ["https://client.com/a"]


def test_rank_related_urls_prefers_topical_slugs_and_skips_target():
    ranked = rank_related_urls(
        ["https://client.com/walk-in-tubs", "https://client.com/about-us",
         "https://client.com/walk-in-tubs/pricing"],
        ["walk in tub cost"], exclude=[TARGET_URL], limit=5,
    )
    assert ranked == ["https://client.com/walk-in-tubs/pricing"]


def test_load_site_pages_detects_faq_hub_from_page_links():
    pages, notes = load_site_pages(_target(), ["walk in tub"], fetch=fake_fetch)
    urls = {sp.page.url: sp.is_faq_hub for sp in pages}
    assert urls == {
        "https://client.com/faq": True,
        "https://client.com/walk-in-tubs/pricing": False,
    }
    assert "https://client.com/faq" in notes[0]


def test_load_site_pages_respects_user_saying_no_faq():
    pages, notes = load_site_pages(
        _target(), ["walk in tub"], faq_urls=[], fetch=fake_fetch
    )
    assert all(not sp.is_faq_hub for sp in pages)
    assert notes[0].startswith("No general FAQ")


# ── Placement: one home per question ────────────────────────────────────────

def test_same_question_tolerates_rewording():
    assert same_question("Does Medicare pay for a walk-in tub?",
                         "Does Medicare pay for walk-in tubs?")
    assert not same_question("Does Medicare pay for a walk-in tub?",
                             "Do you offer financing?")


def test_question_already_on_the_page_is_not_redrafted():
    draft = _placed("How long does installation take?")
    assert draft.status == "exists_here"


def test_topical_page_elsewhere_gets_full_answer_and_this_page_links():
    draft = _placed("How much does a walk-in tub cost installed?")
    assert draft.status == "linked"
    assert draft.link_url == "https://client.com/walk-in-tubs/pricing"
    # One sentence only — the full answer stays on the pricing page.
    assert draft.answer.startswith("A walk-in tub costs between $4,000")
    assert "Installation labour" not in draft.answer


def test_general_faq_never_outranks_this_page_as_home():
    draft = _placed("Does Medicare pay for a walk-in tub?")
    assert draft.status == "home_here"
    assert draft.source_url == "https://client.com/faq"
    assert "Medicare does not usually cover" in draft.answer
    assert draft.trim_elsewhere == ["https://client.com/faq"]
    assert draft.trim_questions == {"https://client.com/faq": "Does Medicare pay for a walk-in tub?"}


def test_question_nobody_answers_stays_a_gap():
    draft = _placed("What are the drawbacks of a walk-in tub?")
    assert draft.status == "gap" and draft.page_gap


def test_outputs_carry_placement_and_trim_list():
    drafts = [
        _placed("How much does a walk-in tub cost installed?"),
        _placed("Does Medicare pay for a walk-in tub?"),
        _placed("What are the drawbacks of a walk-in tub?"),
        _placed("How long does installation take?"),
    ]
    md = render_markdown(_target(), drafts, "informational", "x", 0.004,
                         site_notes=["checked"])
    assert ("Short answer on this page; the full answer stays on "
            "https://client.com/walk-in-tubs/pricing. Nothing changes on") in md
    assert ('**Edit another page:** on https://client.com/faq, the question "Does '
            'Medicare pay for a walk-in tub?" now has its full answer on this page. '
            'Remove the full answer there and keep at most one sentence linking '
            "here; remove the question from that page's FAQPage markup, if it has any") in md
    assert "- **Already answered on this page:** How long does installation take?" in md
    assert "**No passage on the site uses this question's key words.**" in md
    assert ("Full answer on this page, moved from https://client.com/faq, which "
            "publishes the same question") in md

    names = [d.question.question for d in drafts if d.status in CANDIDATE]
    assert names == [
        "How much does a walk-in tub cost installed?",
        "Does Medicare pay for a walk-in tub?",
    ]


# ── Concept gaps vs competitors ─────────────────────────────────────────────

def test_concept_gaps_skip_covered_and_boilerplate_headings():
    comp_a = parse_html("https://a.com/tubs", """<body>
      <h2>Medicaid waivers for walk-in tubs</h2><h2>Built-in seating</h2>
      <h2>Related posts</h2></body>""")
    comp_b = parse_html("https://b.com/tubs", """<body>
      <h2>Using Medicaid waivers</h2><h3>Tub door seal maintenance</h3></body>""")
    site_faq = parse_html("https://client.com/faq", FAQ_HTML)
    gaps = find_concept_gaps(_target(), [comp_a, comp_b], ["walk in tub"],
                             site_pages=[site_faq])
    concepts = [g.concept for g in gaps]
    assert concepts[0] == "Medicaid waivers for walk-in tubs"
    assert gaps[0].competitors == ["a.com", "b.com"]
    assert gaps[0].covered_at == "https://client.com/faq"
    assert "Built-in seating" not in concepts        # the page covers it
    assert "Related posts" not in concepts           # page furniture
    assert "Tub door seal maintenance" in concepts


# ── Intake ───────────────────────────────────────────────────────────────────

def test_intake_asks_for_everything_not_given(monkeypatch):
    monkeypatch.setattr(faqgen, "fetch_page", lambda url, user_agent="": _target())
    monkeypatch.setattr(faqgen, "fetch_html", fake_fetch)
    answers = iter([
        TARGET_URL,                         # page
        "none",                             # AI citation data
        "walk in tub cost, walk in tub medicare",
        "is a walk-in tub worth it for seniors; walk in tub vs shower",
        "canada",                           # market
        "5",                                # questions
        "",                                 # FAQ page: accept detected
        "https://client.com/walk-in-tubs/pricing",
    ])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    args = faqgen.build_parser().parse_args([])
    page, keywords, faq_urls = faqgen.intake(args, interactive=True)
    assert keywords == ["walk in tub cost", "walk in tub medicare"]
    assert args.prompt == ["is a walk-in tub worth it for seniors", "walk in tub vs shower"]
    assert (args.location, args.max_questions) == ("canada", 5)
    assert faq_urls == ["https://client.com/faq"]
    assert args.site_page == ["https://client.com/walk-in-tubs/pricing"]


def test_intake_without_terminal_uses_flags_and_defaults(monkeypatch):
    monkeypatch.setattr(faqgen, "fetch_page", lambda url, user_agent="": _target())
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(
        AssertionError("must not prompt")))
    args = faqgen.build_parser().parse_args([TARGET_URL, "--faq-url", "none"])
    _, keywords, faq_urls = faqgen.intake(args, interactive=False)
    assert keywords == ["walk-in tubs"] and args.prompt == []
    assert (args.location, args.max_questions, faq_urls) == ("us", 8, [])


def test_rank_related_urls_skips_translations_of_the_site():
    urls = ["https://client.com/fr/walk-in-tubs/pricing",
            "https://client.com/walk-in-tubs/pricing"]
    assert rank_related_urls(urls, ["walk in tub"], [TARGET_URL], 5, language="") == [
        "https://client.com/walk-in-tubs/pricing"]
    assert rank_related_urls(urls, ["walk in tub"], [], 5, language="fr") == [
        "https://client.com/fr/walk-in-tubs/pricing"]


def test_concept_gaps_drop_competitor_brand_headings():
    comp = parse_html("https://www.rivalclinic.com/x", """<body>
      <h2>Why patients trust RivalClinic</h2><h2>Licensed tub installers</h2></body>""")
    gaps = find_concept_gaps(_target(), [comp], ["walk in tub"])
    assert [g.concept for g in gaps] == ["Licensed tub installers"]


def test_keyword_suggestions_put_title_first_when_h1_is_a_slogan():
    page = parse_html("https://x.com/program", """<html><head>
      <title>Walk-In Tub Installation Service | Example Co</title></head>
      <body><h1>A bathroom built around your safety, your comfort, and your home.</h1></body></html>""")
    assert page.keyword_suggestions() == [
        "walk-in tub installation service",
        "a bathroom built around your safety, your comfort, and your home",
    ]
    assert _target().keyword_suggestions() == ["walk-in tubs"]


# ── The user's own prompts ───────────────────────────────────────────────────

def test_prompts_become_questions_that_outrank_google():
    from faqgen_lib.drafting import prioritize_questions
    from faqgen_lib.serp import prompt_questions
    paa = [PAAQuestion(question=f"PAA question {i}?", rank=i, keyword="kw")
           for i in range(1, 4)]
    prompts = prompt_questions(["is a walk-in tub worth it for seniors", "  "])
    assert prompts[0].question == "Is a walk-in tub worth it for seniors?"
    ranked = prioritize_questions(prompts + paa, _target(), 3)
    assert ranked[0].question.from_prompt
    assert len(ranked) == 3


def test_prompt_matching_a_paa_question_keeps_the_prompt_and_notes_google():
    from faqgen_lib.drafting import prioritize_questions
    from faqgen_lib.render import _origin
    from faqgen_lib.serp import prompt_questions
    prompts = prompt_questions(["Does Medicare pay for a walk-in tub?"])
    paa = [PAAQuestion(question="Does Medicare pay for a walk-in tub?", rank=2,
                       keyword="walk in tub")]
    [draft] = prioritize_questions(prompts + paa, _target(), 5)
    assert draft.question.from_prompt
    assert _origin(draft) == ('your prompt "Does Medicare pay for a walk-in tub?" · '
                              'Google also asks it for "walk in tub"')


def test_draft_opens_with_what_to_do_before_the_answers():
    drafts = [
        _placed("How much does a walk-in tub cost installed?"),
        _placed("Does Medicare pay for a walk-in tub?"),
        _placed("What are the drawbacks of a walk-in tub?"),
    ]
    md = render_markdown(_target(), drafts, "informational", "x", 0.004,
                         site_notes=["checked"])
    order = [md.index(h) for h in ("**In short:**", "## What to do",
                                   "## Candidate answers: verify before publishing",
                                   "## Content to write", "## Appendix")]
    assert order == sorted(order)
    assert ("**In short:** 2 candidate answers to verify · 1 need new content · 0 need your "
            "decision · 0 already on the page.") in md
    assert "### 1. What are the drawbacks of a walk-in tub?" in md[md.index("## Content to write"):]
    assert "| Your prompts | None given |" in md
    assert "—" not in md.replace(_target().url, "")   # plain punctuation, gate-safe


# ── Matching and decoding fixes (live check, 2026-10-22) ──────────────────────

def test_numbers_stay_distinctive_in_questions():
    from faqgen_lib.drafting import tokenize
    assert "60-30-10" in tokenize("What is the 60-30-10 rule for bathroom colours?")
    assert "15" in tokenize("Is it a 15 minute fill?")
    page = parse_html("https://client.com/colours", """<body><h1>Colours</h1>
      <p>The famous rule of thirds for bathroom colours was never a real rule, and
      no designer behind the rule has ever been found by anyone looking.</p></body>""")
    draft = draft_answer(FAQDraft(question=PAAQuestion(
        question="What is the 60-30-10 rule for bathroom colours?", rank=1), score=0), page)
    assert draft.page_gap


def test_utf8_page_without_charset_header_is_not_garbled():
    from faqgen_lib.page import _decoded

    class Resp:
        headers = {"Content-Type": "text/html"}
        content = ('<html><head><meta charset="utf-8"></head><body>'
                   "<p>Today’s strategies</p></body></html>").encode("utf-8")
        text = content.decode("iso-8859-1")

    assert "Today’s strategies" in _decoded(Resp())


# ── Precision fixes (live batch run, 2026-10-22) ─────────────────────────────

_ARTICLE = """<body><h1>Walk-in tub bathroom remodels</h1>
<p>A walk-in tub changes how a bathroom works in every remodel, from small
apartments to large family homes.</p>
<p>Walk-in tub owners see the bathroom change most in daily routines, especially
in older homes chosen for walk-in tub installs.</p>
<p>Grab bars can complement the walk-in tub, making the bathroom safer without
changing the rest of the room.</p></body>"""


def test_question_made_only_of_topic_words_is_flagged_for_a_human():
    page = parse_html("https://client.com/remodels", _ARTICLE)
    draft = draft_answer(FAQDraft(question=PAAQuestion(
        question="What does a walk-in tub do in a bathroom?", rank=1), score=0), page)
    assert draft.status == "check" and draft.page_gap
    assert "walk-in tub" in draft.near_miss_excerpt
    import json
    assert draft.status not in CANDIDATE


def test_short_answer_uses_the_matching_sentence_not_the_first():
    from faqgen_lib.drafting import best_sentences, tokenize
    para = ("The Client Co tub is a low-threshold model. It is built for small bathrooms. "
            "It heats water faster to reduce running costs.")
    assert best_sentences(para, tokenize("How to reduce running costs?"), 1) == \
        "It heats water faster to reduce running costs."


def test_off_topic_google_questions_are_dropped_and_prompts_kept():
    from faqgen_lib.drafting import split_off_topic
    page = parse_html("https://client.com/x", "<html><head><title>Walk-In Tub "
                      "Installation | Co</title></head><body><h1>Walk-in tub installation</h1></body></html>")
    qs = [PAAQuestion(question="Which organ dies first before death?", rank=1),
          PAAQuestion(question="Why does my tub leak after installation?", rank=2),
          PAAQuestion(question="Which organ fails first?", rank=3, from_prompt=True)]
    kept, dropped = split_off_topic(qs, page, ["walk in tub installation"])
    assert [q.rank for q in kept] == [2, 3]
    assert [q.rank for q in dropped] == [1]


def test_reworded_duplicates_merge():
    from faqgen_lib.drafting import prioritize_questions
    qs = [PAAQuestion(question="What is the disadvantage of walk-in tub doors?", rank=1, keyword="a"),
          PAAQuestion(question="What is a disadvantage of walk-in tub doors?", rank=2, keyword="b")]
    [d] = prioritize_questions(qs, _target(), 5)
    assert d.surfaced_by == ["a", "b"]


def test_concept_gaps_drop_bylines_and_paper_sections():
    comp = parse_html("https://journal.org/x", """<body>
      <h2>Mara Lindqvist</h2><h2>Tomas R Feld , PhD</h2><h2>1 Introduction</h2>
      <h2>Similar content being viewed by others</h2><h2>Recently viewed</h2>
      <h2>Priya K Nair , MS</h2><h2>Lucia Rosa do Vale</h2>
      <h2>Table 1. Tub fill times by model.</h2><h2>Statements and Declarations</h2>
      <h2>Gel coat versus acrylic tub shells</h2></body>""")
    gaps = find_concept_gaps(_target(), [comp], ["walk in tub"])
    assert [g.concept for g in gaps] == ["Gel coat versus acrylic tub shells"]


def test_single_competitor_offtopic_headings_are_dropped():
    comp = parse_html("https://journal.org/x", """<body>
      <h2>Partnering for Success</h2><h2>Tub door seal maintenance</h2></body>""")
    gaps = find_concept_gaps(_target(), [comp], ["walk in tub"])
    assert [g.concept for g in gaps] == ["Tub door seal maintenance"]


def test_definition_questions_are_never_auto_answered():
    from faqgen_lib.drafting import _DEFINITIONAL
    for q in ["What is walk-in tub installation?",
              "What does a walk-in tub do in a bathroom?",
              "How does a walk-in tub work?"]:
        assert _DEFINITIONAL.match(q), q
    for q in ["What are the best ways to shorten a walk-in tub's fill time?",
              "What is the disadvantage of walk-in tub doors?",
              "How much does a walk-in tub cost installed?"]:
        assert not _DEFINITIONAL.match(q), q
    page = parse_html("https://client.com/v", """<body><h1>Installation</h1>
      <p>Walk-in tub installation takes two days in most bathrooms.</p></body>""")
    draft = draft_answer(FAQDraft(question=PAAQuestion(
        question="What is walk-in tub installation?", rank=1), score=0), page)
    assert draft.status == "check"


def test_possible_answer_on_another_page_is_surfaced_for_checking():
    target = _target()
    article = SitePage(page=parse_html("https://client.com/blog/door-seals", """<body>
      <h1>Tub door seals</h1>
      <p>A sealed threshold keeps the water inside the tub once the door closes.</p>
      </body>"""))
    draft = draft_answer(FAQDraft(question=PAAQuestion(
        question="What is a sealed threshold?", rank=1), score=0), target)
    assert draft.status == "gap"                     # nothing on the target page
    draft = place_answer(draft, target, [article])
    assert draft.status == "check"
    assert draft.source_url == "https://client.com/blog/door-seals"
    assert "keeps the water inside" in draft.near_miss_excerpt


def test_prompt_answered_in_other_words_goes_to_decision_not_content_to_write():
    """Live run 2026-10-22: a "who ... and how long?" prompt was called
    unanswered next to a passage that answered both in other words."""
    from faqgen_lib.runner import RunOptions, build_page_faq
    from faqgen_lib.serp import SerpData
    page = parse_html("https://client.com/program", """<body><h1>The program</h1>
      <p>Client Co installs walk-in tubs in two days, with a certified fitter who
      handles the plumbing and the tiling.</p></body>""")

    class NoSerps:
        def get(self, kw):
            return SerpData(keyword=kw)

    result = build_page_faq(
        page, [], ["Who fits Client Co's walk-in tubs and how long is it?"], NoSerps(),
        RunOptions(concepts=False, log=lambda m: None), site_pages=[])
    [draft] = result["drafts"]
    assert draft.status == "check"
    assert "two days" in draft.near_miss_excerpt


def test_visible_answer_wins_over_differing_faq_markup():
    page = parse_html("https://client.com/guide", """<body>
      <script type="application/ld+json">{"@type":"FAQPage","mainEntity":[
        {"@type":"Question","name":"How long does installation take?",
         "acceptedAnswer":{"@type":"Answer","text":"Usually 23 days."}}]}</script>
      <details><summary>How long does installation take?</summary>
        <p>Usually 2-3 days.</p></details></body>""")
    assert page.qa_pairs == [("How long does installation take?", "Usually 2-3 days.")]


def test_spelling_variants_match():
    from faqgen_lib.drafting import tokenize
    assert tokenize("Is a fiber tub liner worth it?") & tokenize("fibre liners")
    assert tokenize("behavior change") == tokenize("behaviour change")


# ── Page reader: short text blocks (live pricing page, 2026-10-22) ───────────

def test_reader_keeps_short_text_blocks_but_not_chrome():
    page = parse_html("https://client.com/pricing", """<body class="theme-modal-ready page">
      <header role="banner"><div>Sign in to your account today</div></header>
      <h1>Flexible plans</h1>
      <div class="card"><div>Pay in Full $749 total</div>
        <div>Try it risk-free with our 14-day, 100% refund guarantee.</div>
        <span>Choose</span>
        <a href="/book"><div>Book a free consultation now</div></a></div>
      <div class="cookie-banner"><div>We use cookies to improve your experience on this site.</div></div>
      <div role="dialog"><div>Subscribe to our newsletter for weekly tips</div></div>
      <div aria-hidden="true"><div>Hidden duplicate of the guarantee line here.</div></div>
      <p>Short p.</p>
      <ul><li>A list item that is long enough to be kept as a paragraph by the old rule.</li></ul>
      </body>""")
    texts = [t for t, _ in page.paragraphs]
    assert "Pay in Full $749 total" in texts
    assert "Try it risk-free with our 14-day, 100% refund guarantee." in texts
    assert page.paragraphs[0][1] == "Flexible plans"           # heading context kept
    for junk in ("Sign in", "Choose", "Book a free", "cookies", "newsletter", "Hidden", "Short p."):
        assert not any(junk in t for t in texts), junk
    assert any(t.startswith("A list item") for t in texts)
