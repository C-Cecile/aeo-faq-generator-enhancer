#!/usr/bin/env python3
"""AEO FAQ generator — real People Also Ask questions, answers grounded in the page.

Usage:
    python faqgen.py                       # asks for the page, queries and FAQ page
    python faqgen.py --plan https://client.com --section /blog/   # many pages: free plan
    python faqgen.py --batch output/.../batch-plan.csv            # then run it
    python faqgen.py https://client.com/page [--keyword "target phrase"] [options]

Requires DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD in ./.env (see .env.example).
"""

import argparse
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from faqgen_lib import FaqgenError
from faqgen_lib.batch import (
    DEFAULT_BUDGET_FACTOR,
    MAX_QUERIES,
    build_plan,
    estimate_batch,
    pages_for_plan,
    read_plan,
    write_plan,
)
from faqgen_lib import citations as citation_data
from faqgen_lib.credentials import resolve_transport
from faqgen_lib.page import fetch_html, fetch_page
from faqgen_lib.render import output_dir_for, write_outputs
from faqgen_lib.runner import (
    RunOptions,
    SerpCache,
    build_page_faq,
    run_batch as run_pages,
    summary_counts,
)
from faqgen_lib.serp import LOCATION_CODES, DataForSEOClient, estimate_cost
from faqgen_lib.site import detect_faq_hubs, load_site_pages

TOOL_DIR = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="faqgen",
        description="Generate an AEO FAQ draft from Google People Also Ask data.",
    )
    parser.add_argument(
        "url", nargs="?",
        help="Page URL to build FAQs for (omit when using --check)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Report how the tool reaches DataForSEO and confirm it works, then exit. "
             "Costs nothing. Run this first if you're not sure your setup is right.",
    )
    parser.add_argument(
        "--sandbox", action="store_true",
        help="Use DataForSEO's free sandbox instead of the live API. Same endpoints "
             "and response structure, placeholder values, never charged. Proves the "
             "whole pipeline works without spending; useless for real answers.",
    )
    parser.add_argument(
        "--keyword", action="append", default=[], metavar="PHRASE",
        help=f"Target keyword (repeatable, max {MAX_QUERIES}; default: derived from the page title/h1)",
    )
    parser.add_argument(
        "--prompt", action="append", default=None, metavar="TEXT",
        help="An exact prompt or question to cover (repeatable). Goes into the "
             "FAQ ahead of Google's questions; not looked up, so it costs nothing.",
    )
    parser.add_argument(
        "--citations", action="append", default=None, metavar="PATH",
        help="AI citation data: an export file or folder from Profound or another "
             "AI visibility tool (repeatable). Default: any file in ./citations/, or "
             "with 'citation' or 'fanout' in its name, here or next to this tool.",
    )
    parser.add_argument(
        "--no-citations", action="store_true",
        help="Don't look for or use AI citation data",
    )
    parser.add_argument(
        "--location", default=os.environ.get("FAQGEN_LOCATION"),
        choices=sorted(LOCATION_CODES), help="Market (default: us)",
    )
    parser.add_argument("--language", default=os.environ.get("FAQGEN_LANGUAGE", "en"))
    parser.add_argument(
        "--paa-depth", type=int, default=2, choices=range(0, 5), metavar="0-4",
        help="People Also Ask click depth (0 = base PAA only, cheapest; default 2)",
    )
    parser.add_argument("--max-questions", type=int, default=None,
                        help="How many questions to keep (default 8)")
    parser.add_argument(
        "--faq-url", action="append", default=None, metavar="URL",
        help="The site's general FAQ page (repeatable). Default: detected from "
             "the page's links or /faq. Pass 'none' if the site has none.",
    )
    parser.add_argument(
        "--site-page", action="append", default=[], metavar="URL",
        help="Another page on the site that covers this topic (repeatable)",
    )
    parser.add_argument(
        "--no-site-check", action="store_true",
        help="Skip checking the rest of the site for questions already answered",
    )
    parser.add_argument(
        "--no-concepts", action="store_true",
        help="Skip the concept-gap comparison with the top-ranking competitors",
    )
    batch = parser.add_argument_group("many pages")
    batch.add_argument(
        "--plan", metavar="SITE_OR_FILE",
        help="Step 1 for many pages, free: list pages from a site's sitemap (or a "
             "text file of URLs), suggest a query for each, write batch-plan.csv "
             "and print the estimated cost. Edit the plan, then use --batch.",
    )
    batch.add_argument(
        "--section", default="", metavar="PATH",
        help="With --plan on a site: only pages under this path, e.g. /blog/",
    )
    batch.add_argument("--max-pages", type=int, default=30,
                       help="With --plan: at most this many pages (default 30)")
    batch.add_argument(
        "--batch", type=Path, metavar="PLAN_CSV",
        help="Step 2: run a reviewed batch-plan.csv. Shows the estimate and asks first.",
    )
    batch.add_argument(
        "--budget", type=float, metavar="USD",
        help="With --batch: stop before spending more than this "
             f"(default {DEFAULT_BUDGET_FACTOR}x the estimate)",
    )
    parser.add_argument(
        "--no-input", action="store_true",
        help="Never ask questions; use flags and defaults (for scripts)",
    )
    parser.add_argument(
        "--user-agent", default="", metavar="UA",
        help="User agent for fetching the page. Only needed when a site refuses "
             "the default browser-like one.",
    )
    parser.add_argument("--out", type=Path, default=TOOL_DIR / "output")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch the page and show keywords + cost estimate without calling DataForSEO",
    )
    return parser


def run_check(sandbox: bool = False) -> int:
    """Report the route to DataForSEO and prove it works. Costs nothing."""
    transport = resolve_transport(sandbox=sandbox)
    # Flushed so it can't land after a failure written to stderr.
    print(f"Route found: {transport.describe()}\n", flush=True)

    data = DataForSEOClient(transport).check()
    status = data.get("status_code")
    if status != 20000:
        raise FaqgenError(
            f"Reached DataForSEO but it rejected the request: "
            f"{status} {data.get('status_message', '')}\n"
            "That usually means the credentials are wrong or the account is inactive."
        )

    print("DataForSEO answered: 20000 Ok — you're ready to go.")
    try:
        money = data["tasks"][0]["result"][0]["money"]
        print(f"Account balance: ${money.get('balance', 0):.2f}")
    except (KeyError, IndexError, TypeError):
        pass
    print(f"\nA typical run costs about ${estimate_cost(1):.4f} per keyword.")
    print("Try it:  python faqgen.py https://example.com/a-page")
    return 0


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def _split(text: str) -> list:
    return [part.strip() for part in text.split(",") if part.strip()]


def settle_citations(args, interactive: bool):
    """Find, offer and load AI citation data. Sets and returns args.citation_data."""
    args.citation_data = None
    if args.no_citations:
        return None
    paths = citation_data.discover(
        [Path(p) for p in args.citations or []],
        search_dirs=[Path.cwd(), TOOL_DIR],
    )
    if interactive and not args.citations:
        print("\nAI citation data shows which of the site's pages assistants like")
        print("ChatGPT already cite, and for which prompts. With it, the FAQ builds on")
        print("the pages that are cited, and suggests prompts the site is missing.")
        if paths:
            print("Found: " + ", ".join(p.name for p in paths))
            answer = ask("Use these? Enter = yes, 'none', or another file/folder", "yes")
        else:
            print("Do you have exports from Profound or another AI visibility tool")
            print("(citations, fan-out searches; JSON or CSV)?")
            answer = ask("File or folder (Enter for none)", "none")
        if answer.lower() in ("none", "no", "n"):
            return None
        if answer.lower() not in ("yes", "y"):
            paths = citation_data.discover([Path(answer).expanduser()])
    data = citation_data.load(paths)
    if data:
        print(f"Using AI citation data: {data.describe()}")
        args.citation_data = data
    elif args.citations:
        print("No citation rows found in " + ", ".join(args.citations)
              + " (needs a URL column and a prompt column).")
    return args.citation_data


def intake(args, interactive: bool):
    """Settle the page, target queries, market, size and FAQ page before spending.

    Anything given as a flag is taken as answered; the rest is asked when
    running in a terminal, or defaulted (and said so) when not.
    """
    if not args.url:
        if not interactive:
            raise FaqgenError("Give a page URL, or use --check to test your setup.")
        args.url = ask("Which page is this FAQ for? (full URL)")
        if not args.url:
            raise FaqgenError("No page given.")

    page = fetch_page(args.url, user_agent=args.user_agent)
    print(f"Page: {page.h1 or page.title}")
    data = settle_citations(args, interactive)
    weak = data.weak_prompts(page.url, page.keyword_suggestions()) if data else []

    keywords = [k.strip() for k in args.keyword if k.strip()][:MAX_QUERIES]
    if not keywords:
        derived = page.derived_keyword()
        if interactive:
            suggestions = page.keyword_suggestions()
            print(f"\nWhich search queries should this FAQ target? Up to {MAX_QUERIES}, one paid")
            print("Google lookup each. If you know the page's top query in Search")
            print("Console, use that. From the page itself:")
            for s in suggestions:
                print(f"  - {s}")
            fanouts = data.fanout_queries([p for p, _, _ in weak]) if data else []
            if fanouts:
                print("Searches AI assistants ran for prompts on this topic:")
                for s in fanouts:
                    print(f"  - {s}")
            default = suggestions[0] if suggestions else derived
            keywords = _split(ask("Queries, comma-separated", default))[:MAX_QUERIES]
        elif derived:
            keywords = [derived]
            print(f"No --keyword given; using page-derived keyword: {derived!r}")
        if not keywords:
            raise FaqgenError(
                "Could not derive a keyword from the page title/h1 — "
                "pass one with --keyword."
            )

    if args.prompt is None:
        args.prompt = []
        if interactive:
            print("\nAny exact prompts or questions you want this FAQ to cover, for")
            print("example from an AI visibility prompt plan? They go in ahead of")
            print("Google's questions and cost nothing. Separate with ';'.")
            if weak:
                print("Prompts on this topic where AI assistants rarely cite the site")
                print("(from the citation data; type their numbers to include them):")
                for i, (prompt, rate, topic) in enumerate(weak, 1):
                    label = f", topic: {topic}" if topic else ""
                    print(f"  {i}. {prompt} (site cited in {rate:.0%} of citations{label})")
            answer = ask("Prompts or numbers (Enter to skip)")
            for part in re.split(r"[;,]", answer) if weak else answer.split(";"):
                part = part.strip()
                if part.isdigit() and weak and 1 <= int(part) <= len(weak):
                    args.prompt.append(weak[int(part) - 1][0])
                elif part:
                    args.prompt.append(part)

    if not args.location:
        args.location = "us"
        if interactive:
            choice = ask(f"Market ({', '.join(sorted(LOCATION_CODES))})", "us").lower()
            if choice not in LOCATION_CODES:
                raise FaqgenError(f"Unknown market {choice!r}.")
            args.location = choice

    if args.max_questions is None:
        args.max_questions = 8
        if interactive:
            answer = ask("How many questions", "8")
            if not answer.isdigit() or int(answer) < 1:
                raise FaqgenError(f"{answer!r} isn't a number of questions.")
            args.max_questions = int(answer)

    faq_urls = None
    if args.faq_url is not None:
        faq_urls = [u for u in args.faq_url if u.lower() != "none"]
    if not args.no_site_check and interactive and faq_urls is None:
        detected = detect_faq_hubs(page, fetch_html)
        print("\nDoes the site have a general FAQ page? Questions it already answers")
        print("won't be drafted again at full length.")
        answer = ask("FAQ page URL, or 'none'", ", ".join(detected) or "none")
        faq_urls = [] if answer.lower() == "none" else _split(answer)
        more = ask("Any other pages covering this topic? (URLs, Enter to skip)")
        args.site_page = [*args.site_page, *_split(more)]

    return page, keywords, faq_urls


def options_from(args, faq_urls=None, sandbox: bool = False) -> RunOptions:
    return RunOptions(
        location=args.location, language=args.language, paa_depth=args.paa_depth,
        max_questions=args.max_questions, concepts=not args.no_concepts,
        site_check=not args.no_site_check, faq_urls=faq_urls,
        citations=getattr(args, "citation_data", None),
        extra_site_pages=list(args.site_page), user_agent=args.user_agent,
        sandbox=sandbox,
    )


def connect(args):
    transport = resolve_transport(sandbox=args.sandbox)
    print(f"Reaching DataForSEO via {transport.describe()}")
    if transport.is_sandbox:
        print(
            "\n*** SANDBOX MODE — the questions and answers below are placeholder\n"
            "*** data, not real Google results. Use this to prove the setup works,\n"
            "*** never as a deliverable. Re-run without --sandbox for real data.\n"
        )
    return transport


def run_plan(args, interactive: bool) -> int:
    """Free: pick the pages, suggest a query each, write the plan, show the cost."""
    print(f"Listing pages from {args.plan}{' under ' + args.section if args.section else ''}…")
    urls = pages_for_plan(args.plan, args.section, args.max_pages)
    if not urls:
        raise FaqgenError(
            "No pages found. Check the site has a sitemap (or the file lists URLs), "
            "and that --section matches the start of their paths."
        )
    data = settle_citations(args, interactive)
    print(f"Reading {len(urls)} pages to suggest a query for each (free)…")
    rows = build_plan(urls, citations=data)
    path = write_plan(rows, output_dir_for(urls[0], args.out) / "batch-plan.csv")
    print(f"\n{estimate_batch(rows).describe()}")
    print(f"\nPlan written: {path}")
    print("Open it in Sheets or Excel and check each row before spending anything:")
    print(f"  queries — up to {MAX_QUERIES} per page, separated by ';' (one paid lookup each,")
    print("            shared queries paid once). Clear a row's queries to skip it.")
    print("  prompts — exact prompts to cover, separated by ';' (free).")
    print(f"Then run:  python faqgen.py --batch {path}")
    return 0


def run_batch(args, interactive: bool) -> int:
    rows = read_plan(args.batch)
    if not rows:
        raise FaqgenError(f"No pages in {args.batch}.")
    settle_citations(args, interactive)
    estimate = estimate_batch(rows)
    print(estimate.describe())

    if not args.location:
        args.location = "us"
        if interactive:
            args.location = ask(
                f"Market ({', '.join(sorted(LOCATION_CODES))})", "us").lower()
            if args.location not in LOCATION_CODES:
                raise FaqgenError(f"Unknown market {args.location!r}.")
    if args.max_questions is None:
        args.max_questions = 8
        if interactive:
            answer = ask("Questions per page", "8")
            if not answer.isdigit() or int(answer) < 1:
                raise FaqgenError(f"{answer!r} isn't a number of questions.")
            args.max_questions = int(answer)
    args.prompt = args.prompt or []

    budget = args.budget or max(round(estimate.cost * DEFAULT_BUDGET_FACTOR, 2), 0.01)
    print(f"Spending cap: ${budget:.3f} — the run stops before going over it.")
    if args.dry_run:
        print("Dry run — no API calls made.")
        return 0
    if interactive and ask("Go ahead? (y/n)", "y").lower() not in ("y", "yes"):
        print("Stopped — nothing was charged.")
        return 0

    transport = connect(args)
    faq_urls = None
    if args.faq_url is not None:
        faq_urls = [u for u in args.faq_url if u.lower() != "none"]
    result = run_pages(
        rows, DataForSEOClient(transport), args.batch.parent, budget,
        options_from(args, faq_urls, transport.is_sandbox),
        fetch=lambda url: fetch_html(url, user_agent=args.user_agent),
    )
    print(f"\n{result.drafted} of {len(rows)} pages drafted. "
          f"Actual DataForSEO cost: ${result.cost:.4f} (estimated ~${estimate.cost:.2f}).")
    print(f"Start with: {result.summary_path}")
    return 0


def main(argv=None) -> int:
    load_dotenv(TOOL_DIR / ".env")
    args = build_parser().parse_args(argv)

    if args.check:
        return run_check(sandbox=args.sandbox)

    interactive = sys.stdin.isatty() and not args.no_input
    if args.plan:
        return run_plan(args, interactive)
    if args.batch:
        return run_batch(args, interactive)

    page, keywords, faq_urls = intake(args, interactive)

    est = estimate_cost(len(keywords), args.paa_depth)
    print(f"\nKeywords: {keywords} | location={args.location} "
          f"paa-depth={args.paa_depth} | estimated cost ~${est:.4f}")
    if args.dry_run:
        print("Dry run — no API calls made.")
        return 0
    if interactive and ask("Go ahead? (y/n)", "y").lower() not in ("y", "yes"):
        print("Stopped — nothing was charged.")
        return 0

    transport = connect(args)
    options = options_from(args, faq_urls, transport.is_sandbox)
    serps = SerpCache(DataForSEOClient(transport), options)
    for kw in keywords:
        serps.get(kw)

    site_notes, site_pages = None, None
    if not args.no_site_check:
        print("Checking the rest of the site for questions already answered…")
        site_pages, site_notes = load_site_pages(
            page, keywords, faq_urls=faq_urls, extra_urls=args.site_page,
        )
    result = build_page_faq(page, keywords, args.prompt, serps, options, site_pages)
    drafts, existing = result["drafts"], result["existing"]

    out_dir = output_dir_for(args.url, args.out)
    written = write_outputs(
        out_dir, page, drafts + existing, result["intent"], result["rationale"],
        serps.cost, serps.raw, result["provenance"], transport.is_sandbox,
        site_notes, result["concept_gaps"], result["off_topic"],
        meta={"market": args.location, "language": args.language,
              "queries": keywords, "prompts": args.prompt,
              "citations": args.citation_data.describe() if args.citation_data else ""},
    )

    counts = summary_counts(drafts)
    print(f"\nIntent: {result['intent']} ({result['rationale']})")
    print(f"{counts['answered']} answered from this page, "
          f"{counts['home_here']} answered here from another page's material, "
          f"{counts['linked']} short answers linking to their home page, "
          f"{counts['check']} to check by hand, {counts['gap']} flagged as GAP.")
    if existing:
        print(f"{len(existing)} skipped: the page already answers them.")
    if result["concept_gaps"]:
        print(f"{len(result['concept_gaps'])} concept gaps vs competitors listed in the draft.")
    print(f"Actual DataForSEO cost: ${serps.cost:.4f}")
    print("Outputs:")
    for path in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FaqgenError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
