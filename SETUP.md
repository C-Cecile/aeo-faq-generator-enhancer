# AEO FAQ Generator — setup

Builds an FAQ section for a web page from Google's real "People also ask"
questions, with answers taken only from the site's own content. It asks which
page, which queries and which prompts to target before it spends anything, and
checks the rest of the site so a question already answered elsewhere gets a
short linked answer instead of a duplicate.

It runs on **your own** Claude and DataForSEO accounts. Each Google lookup costs
about $0.004 on your DataForSEO balance; a typical page is 1–3 lookups.

There are two ways to run it. Pick one.

---

## Option 1 — in claude.ai (no install, recommended)

1. **DataForSEO account.** Sign up at <https://dataforseo.com>. New accounts get
   a small free trial balance ($1 at the time of writing, about 250 lookups).
   Your API login and password are on <https://app.dataforseo.com/api-access>.
   They are not the password you sign in to the website with.
2. **Connect DataForSEO to Claude.** In claude.ai: Settings → Connectors → Add
   custom connector. Use the address in DataForSEO's guide
   (<https://dataforseo.com/help-center/connect-claude-to-dataforseo-mcp-very-simple-guide>;
   at the time of writing `https://mcp.dataforseo.com/http`) and sign in with
   your API login when asked.
3. **Turn on code execution, and let it reach the sites.** Settings →
   Capabilities → Code execution and file creation. (Team or Enterprise plan:
   an admin may need to allow skills and custom connectors first.) Then set
   its domain allowlist to either **All domains**, or the client's site plus
   its competitors' sites. The default, "Package managers only", blocks the
   sites, and the skill then reads far less of them: no full sitemap, no page
   markup, no competitor pages.
4. **Upload the skill.** Settings → Capabilities → Skills → Upload skill → choose
   `aeo-faq.zip` (next to this file).
5. **Try it.** Start a new chat and type:
   `Build FAQs for https://your-site.com/a-page`

Using **Claude Code** instead? If you cloned the repository, there is nothing
to install: the skill is in `.claude/skills/aeo-faq/` and Claude Code picks it
up when you open the folder. Type `/aeo-faq` to start.

The skill reports cost as an estimate on this route, because the connector does
not return the exact charge. Your real spend is in the DataForSEO dashboard.

**Never paste your DataForSEO login or password into a chat.** The connector
handles sign-in; the skill never needs to see them.

---

## Option 2 — the Python command-line tool

For running on your own computer, or many pages at once without using Claude
usage. Needs Python 3.9 or newer.

```bash
cd faq-generator
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then put your DataForSEO API login/password in .env
python faqgen.py --check           # free: confirms your setup works
python faqgen.py                   # asks for the page, queries and prompts
```

Many pages at once (plan for free, see the cost, then run):

```bash
python faqgen.py --plan https://your-site.com --section /blog/
python faqgen.py --batch output/<site>-<date>/batch-plan.csv
```

Full details are in the tool's `README.md`. Optional check that the install
works: `pip install pytest && python -m pytest tests -q` (no network, no cost).

---

## Optional — AI visibility data (Profound or another tool)

If you have access to Profound or another AI visibility tool (for example
Semrush AI Toolkit, Peec AI, Otterly or Scrunch), export its data for the site
and give it to the tool. It's optional but makes the FAQ noticeably better:

- **Which page gets each answer** is decided by where AI assistants already
  cite the site, instead of guessing from page titles. (In one real check, the
  homepage drew almost all of the site's citations on its core topic and the
  dedicated page almost none.)
- **Prompt suggestions:** it lists prompts on the page's topic where
  assistants rarely cite the site, for you to pick from.
- **Better queries:** it suggests the searches assistants actually ran.

**What to export.** From Profound: the citations export and the query fan-out
export (JSON or CSV), for the client and date range you care about. From other
tools: any export listing cited URLs with the prompt they were cited for;
a branded/unbranded column helps.

**Where to put it.**
- claude.ai: upload the files in the chat when the skill asks (it asks first).
- Python tool: put them in a `citations/` folder inside `faq-generator/`, or
  point to them with `--citations path/to/file-or-folder`. The tool finds that
  folder by itself; `--no-citations` turns it off.

These exports are client data: keep them out of shared drives and git (the
`citations/` folder is already git-ignored).

---

## What you get

- `faq-draft.md` — starts with **What to do** (content to write, other pages to
  shorten and link, questions to check by hand), then the draft answers with
  the passage each one came from.
- `faq-schema.jsonld` (skill) — FAQPage markup for the verified answers only. The
  Python tool doesn't write markup: its `finish-in-claude.md` step returns it once
  Claude has checked each candidate answer.

Questions the site cannot answer are flagged as gaps, never filled in. A page
with mostly gaps isn't a failed run: those gaps are the content to write.
