# AEO FAQ Generator

Builds an FAQ section for any page from **Google's real People Also Ask questions**
and your own prompts, with candidate answers found in the site's own content. It
checks the rest of the site so a question already answered elsewhere gets a short
linked answer instead of a duplicate, and it can use AI visibility exports (e.g.
Profound) to decide which page should own each question.

Cost is about **one to five cents per page** (DataForSEO SERP API, on your own
account), shown before anything is spent.

> **Quickest start:** [`SETUP.md`](SETUP.md). Prefer not to install anything? If
> you have a Claude account, use the `/aeo-faq` skill instead; see
> [`claude-code/README.md`](claude-code/README.md).

---

## Setup (about 5 minutes, once)

You need Python 3.9+ and a DataForSEO account.

```bash
cd faq-generator
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Credentials — whichever way suits you

The tool looks for a working route in this order and uses the first it finds, so
you don't have to match anyone else's setup:

1. **A secrets proxy**, if you run one — it holds the keys and adds the auth
   itself, so nothing here ever sees a credential. Set `SECRETS_PROXY_BASE` if
   it isn't at the devcontainer default, or `SECRETS_PROXY_DISABLE=1` to skip
   this check.
2. **Environment variables.** `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD`, or the
   common variants (`DATAFORSEO_USERNAME`, `DFS_LOGIN`, `DATAFORSEO_PASS`, …).
3. **A single base64 token**, if that's what DataForSEO handed you:
   `DATAFORSEO_AUTH`.
4. **A `.env` file** — checked next to this tool, in your working directory and
   its parents, then `~/.dataforseo.env` and `~/.config/dataforseo/.env`.

For the `.env` route, `cp .env.example .env` and fill it in. Credentials come
from <https://app.dataforseo.com/api-access>.
**Never commit a `.env`** — it is already gitignored.

### Check it works

```bash
python faqgen.py --check
```

This costs nothing. It reports which route it found and confirms DataForSEO
answers, so you know you're set up before spending anything. If it can't find a
route, it prints every option with the exact commands and lists where it looked.

### Try the whole thing for free

```bash
python faqgen.py --sandbox https://your-site.com/a-page
```

DataForSEO's sandbox (`sandbox.dataforseo.com`) accepts the same credentials and
returns the same response structure as the live API, with placeholder values, and
never charges. That exercises the entire pipeline — page fetch, request, parsing,
drafting, schema — so you can prove it all works before spending anything.

The output is meaningless as content. It's labelled as sandbox data and the
schema file is named `faq-schema.SANDBOX-DO-NOT-PUBLISH.jsonld` so it can't be
pasted onto a live page by accident.

A secrets proxy can't serve the sandbox — it forwards to the live host — so
sandbox runs need credentials of your own.

---

## Usage

```bash
python faqgen.py
```

With no arguments it asks, one at a time, before spending anything:

1. which page the FAQ is for,
2. which search queries to target (it suggests one from the page's title — up to
   3, one paid lookup each),
3. any exact prompts or questions you want covered (e.g. from an AI visibility
   prompt plan) — these go into the FAQ ahead of Google's questions and cost
   nothing, since they aren't looked up,
4. which market, and how many questions,
5. whether the site has a general FAQ page (it suggests the one it found), and
   any other pages that cover the topic.

It then shows the estimated cost and waits for a yes. Anything you pass as a flag
is taken as answered and not asked again. In a script or CI, add `--no-input` to
use the flags and defaults without asking.

You can also give the page and queries up front:

```bash
python faqgen.py https://client.com/walk-in-tubs \
    --keyword "walk in tub cost" \
    --keyword "walk in tub installation"
```

Check what it will do and what it will cost, without spending anything:

```bash
python faqgen.py https://client.com/walk-in-tubs --dry-run
```

### Many pages at once

For a whole blog or section, plan first — it's free — then run:

```bash
# 1. Free: list the pages, suggest a query for each, estimate the cost
python faqgen.py --plan https://client.com --section /blog/

# 2. Open output/<site>-<date>/batch-plan.csv in Sheets or Excel and review it:
#    shorten headline-length queries (flagged in the notes column), add up to 5
#    queries per page separated by ';', add exact prompts, clear a row to skip it.

# 3. Run it — shows the estimate again and asks before spending
python faqgen.py --batch output/<site>-<date>/batch-plan.csv
```

`--plan` also takes a text file with one URL per line instead of a site. It skips
category, tag, author and pagination pages, and other-language copies.

Cost is known before you spend: one lookup per **distinct** query, so a query
several pages share is paid once. Reading pages costs nothing. A 30-post blog
at one query per post estimates at about $0.12. The run stops itself
before going over a spending cap — `--budget`, default 1.5× the estimate — and
lists any pages it skipped.

Each page gets its own folder with the usual files, and `batch-summary.md` opens
with what to do across all of them: content to write (each question once, with
the page best placed to answer it), other pages to shorten and link, and a
per-page table. Pages in the batch count as part of the site check, so a
question one page answers becomes a short linked answer on the others.

### Options

| Flag | Default | What it does |
|---|---|---|
| `--keyword` | asked, or derived from title/H1 | Target keyword. Repeatable, max 5. One API call each. |
| `--prompt` | asked, else none | An exact prompt or question to cover. Repeatable. Always kept, ahead of Google's questions; not looked up, so free. |
| `--location` | asked, else `us` | `us`, `canada`, `uk`, `australia`, `france`, `germany` |
| `--language` | `en` | Language code |
| `--paa-depth` | `2` | How deep to expand the PAA box (0–4). `0` is cheapest; higher finds more questions. |
| `--max-questions` | asked, else `8` | How many questions to keep |
| `--faq-url` | detected | The site's general FAQ page. Repeatable. `none` if it has none. |
| `--site-page` | — | Another page covering the topic, to check alongside the sitemap. Repeatable. |
| `--no-site-check` | off | Don't check the rest of the site (target page only, as before) |
| `--no-concepts` | off | Skip the concept-gap comparison with competitors |
| `--no-input` | off | Never ask; use flags and defaults |
| `--citations` | auto | AI citation data: an export file or folder from Profound or another AI visibility tool. Repeatable. Default: `./citations/`, or files named `*citation*`/`*fanout*` |
| `--no-citations` | off | Don't look for or use AI citation data |
| `--plan` | — | Many pages, step 1 (free): a site URL or a file of URLs → `batch-plan.csv` + estimate |
| `--section` | whole site | With `--plan`: only pages under this path, e.g. `/blog/` |
| `--max-pages` | `30` | With `--plan`: at most this many pages |
| `--batch` | — | Many pages, step 2: run a reviewed plan |
| `--budget` | 1.5× estimate | With `--batch`: spending cap in USD |
| `--out` | `./output/` | Where to write results |
| `--user-agent` | browser-like | Only needed when a site refuses automated fetches (403) |
| `--dry-run` | off | Fetch the page, print keywords and cost estimate, make no API calls |
| `--check` | — | Report the credential route and confirm it works, then exit. Free. |

---

## What you get

`faq-draft.md` opens with **What to do** — the counts, the content to write,
the other pages to shorten, and the competitor topics — before the draft answers,
so the recommendations aren't buried at the end.

Each run writes to `output/<domain>-<date>/`:

| File | What it's for |
|---|---|
| `faq-draft.md` | What to do first, then the questions: candidate answers to verify, questions needing your decision, content to write |
| `finish-in-claude.md` | Paste into Claude: it checks each candidate answer, writes the final answers and returns the `FAQPage` markup that matches them |
| `raw.json` | The raw API response, so you can re-check something without paying for the call again |

### About the candidate answers

The tool finds the questions and, for each, the passage on the site that shares
the most of its key words. It can't tell whether that passage **answers** the
question: on a 39-page live run, 14 of 15 passages it matched answered a
different question ("When is CBT not recommended?" matched a passage on why CBT
is recommended). So nothing in `faq-draft.md` is marked ready to publish, and
the tool writes no FAQ markup.

To finish, paste `finish-in-claude.md` into Claude. It decides which candidates
really answer their question, writes the final answers with one quote per
sentence, and returns `FAQPage` markup that matches them. Or run the `/aeo-faq`
skill, which does that judgement as it goes.

### Questions the rest of the site already answers

Before drafting, the tool reads the site's general FAQ page and the sitemap pages
whose URLs best match the topic (up to 12, same language as the page). For every
question it picks a **home**: the page most centred on that topic, judged by
title, H1 and URL. A general FAQ is never the home when a topical page answers
the question too. Then each question gets one status:

| Status | What the draft gives you |
|---|---|
| Answered from this page | The full answer, from this page's text |
| Full answer here, from another page | This page is the better home; the answer is drafted from the other page's text, with its URL |
| Short answer + link | Another page is the home: one sentence here, plus a link to the full answer, so the two pages don't compete |
| Already on this page | The page already asks it; listed, not redrafted |
| Site gap | Nothing on the site answers it (below) |

When this page is the home and another page (often the general FAQ) answers the
same question at length, the draft lists it under **Changes for other pages**:
shorten that answer and link here.

The short answers are extracts — `finish-in-claude.md` tells Claude to reword
them so they don't repeat the home page.

### AI citation data (optional)

Exports from Profound or another AI visibility tool show which of the site's
pages AI assistants cite, and for which prompts. Put them in `citations/`
(git-ignored, since they're client data) or pass `--citations PATH`, and the tool:

- decides which page owns each question from where assistants already cite the
  site on prompts that don't name the brand, and writes the evidence into the
  draft ("AI assistants cite it 12 of 13 times on unbranded prompts about
  this"). Under 5 matching citations it falls back to title, heading and URL,
  and says so;
- suggests prompts on the page's topic where the site is rarely cited;
- suggests the searches assistants ran (fan-outs) as target queries.

Profound's JSON exports are read as they come. Other tools' CSVs need a URL
column and a prompt column; branded/unbranded, topic, quoted text, platform and
date are used when present (common column names are recognised).

### Concept gaps vs competitors

The pages Google ranks for a query are the ones its "People also ask" box and
AI answers draw on. The tool reads the top 3–5 ranking competitor pages (from
the search results it already paid for) and lists the section topics they
cover that this page doesn't. These are FAQ ideas, labelled
as such, not Google questions. `finish-in-claude.md` asks Claude to keep the
relevant ones and turn them into questions only where the client's own material
can answer them. Competitor text is never answer material.

### `[PAGE GAP]` / `[SITE GAP]` flags

When a question comes up in Google but the page has nothing to answer it with, the
tool flags it:

```
[PAGE GAP — the page has no material to answer this; do not fabricate]
Who Google cites for this topic: ncoa.org, kohlerwalkinbath.com, madcitywindows.com
Closest the page gets (under "Pricing") — related, but does not answer
the question: ...
```

The citation list comes from Google's AI Overview for that query: the pages its AI
actually draws on. For AEO work that's the more useful signal — those are the pages
a client has to displace to get cited.

A question is only treated as answered when every distinguishing word in it appears
in the supporting paragraph. "What are the drawbacks of a walk-in tub?" is not
answered by a paragraph that merely describes walk-in tubs — that gets reported as
the closest passage instead, which is what you want for a content brief.

**This is a content recommendation, not an error.** It means people are asking that
question, a competitor is getting the citation, and the client's page needs new
content to compete. Don't invent an answer to fill it in.

Gap-flagged questions are never put in FAQ markup: publishing markup for a
question the page doesn't visibly answer is a markup violation.

---

## Cost

One DataForSEO SERP call per keyword. Measured on live runs (2026-09-23):
**$0.0038 per keyword** at `--paa-depth 2`, returning 9 PAA questions and 8 AI
Overview citations. So a typical one-to-three keyword run costs about one cent.
The site check and the competitor comparison read public web pages, not the
API, so they cost nothing — they add a minute or so to a run.
Every run prints the amount DataForSEO actually charged, read from the API
response — trust that number over any estimate.

---

## Note on schema

Google retired the FAQ rich result in May 2026, so `FAQPage` markup is no longer a
route to expanded snippets. It still helps AI assistants pull clean question/answer
pairs when they cite the page, which is why the tool emits it — and why it only
emits questions the page genuinely answers in visible content.

---

## Running the tests

```bash
python -m pytest tests/ -q
```

Fixture-based, no network calls, no API spend.
