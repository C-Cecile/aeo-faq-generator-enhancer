# `/aeo-faq` — the Claude Code version

Same FAQ workflow as the Python CLI, but it runs inside Claude Code. No Python, no
install, nothing to maintain. If you have a Claude account, this is the easier
option.

The trade-off versus the CLI: Claude writes the answers (grounded in the page, with
the same no-fabrication rules), so the prose comes out polished rather than raw
extraction. It uses your Claude usage instead of running locally.

---

## Quickest route: claude.ai or Claude Desktop

No install, and no API key in any file. You use your own DataForSEO account, so
lookups are billed to you. DataForSEO gives new accounts a small free trial
balance ($1 at the time of writing, roughly 250 lookups at ~$0.004 each).

1. **DataForSEO account.** Sign up at <https://dataforseo.com>. Your API login
   and password are on <https://app.dataforseo.com/api-access>. They are not
   your website password.
2. **Connect DataForSEO to Claude.** In claude.ai: Settings → Connectors → Add
   custom connector, with the URL from DataForSEO's guide
   (<https://dataforseo.com/help-center/connect-claude-to-dataforseo-mcp-very-simple-guide>;
   at the time of writing `https://mcp.dataforseo.com/http`). Sign in with your
   API login when asked. You sign in on DataForSEO's side, so the key never goes
   into a chat.
3. **Turn on code execution, and let it reach the sites.** Settings →
   Capabilities → Code execution and file creation. Skills need it. On Team or
   Enterprise plans, an admin may need to allow skills and custom connectors
   first. Then set its domain allowlist to either **All domains**, or the
   client's site plus its competitors' sites. The default, "Package managers
   only", blocks the sites, and the skill then reads far less of them: no full
   sitemap, no page markup, no competitor pages.
4. **Add the skill.** Settings → Capabilities → Skills → Upload skill, and choose
   `aeo-faq.zip`.
5. **Use it.** In a new chat: "Build FAQs for https://client.com/page". The
   skill asks for the queries, prompts, market and FAQ page before it spends
   anything.

On this route DataForSEO doesn't report the exact charge per call, so the skill
reports the estimate (~$0.004 per query) and says so. Your real spend shows in
the DataForSEO dashboard.

## Start here: where will you run this?

The tool needs a DataForSEO API key, and **how you supply that key safely depends
on where you run it.** Two minutes on this now saves an awkward conversation
later — the wrong answer means a key sitting somewhere your whole team can read.

What counts as "your own machine" is where the *session* runs, not which
interface you use. The terminal, the desktop app and the VS Code or JetBrains
extensions are all the same thing here: your hardware, your filesystem. Only
claude.ai/code is different, because that session runs in Anthropic's cloud.

| Where you run it | Plan | What to do |
|---|---|---|
| **Claude Code on your own machine** (terminal, desktop app, or IDE extension) | any | Any setup works. Follow **Setup** below. |
| **Claude Code on the web** (claude.ai/code) | **Pro or Max** | Use an environment **API credential** — the best option of the three. See [On Claude Code for the web](#on-claude-code-for-the-web). |
| **Claude Code on the web** | **Team or Enterprise** | ⚠ **Don't.** There's no safe way to give a cloud session this key yet. Run Claude Code locally, or use the Python CLI in this repo. |

Two things that are never the answer, wherever you run it:

- **Don't paste the key into the chat.** Anything typed into a conversation
  becomes part of that conversation.
- **Don't put it in a cloud environment's environment variables.** That box warns
  against secrets for a reason: everyone using that environment can read them.

Not sure which plan you're on? It's at the bottom of the left sidebar on
claude.ai, or under Settings → Billing.

---

## Setup

For running Claude Code **on your own machine**. On the web, read
[On Claude Code for the web](#on-claude-code-for-the-web) first — step 2 differs.

1. Copy the `aeo-faq/` folder into `.claude/skills/` in whichever project you're
   working in. (Cloned the public repository? Skip this step: it already has
   `.claude/skills/aeo-faq/`, so the skill is available when you open the
   folder in Claude Code.)

   ```bash
   mkdir -p .claude/skills
   cp -r aeo-faq .claude/skills/
   ```

   This step is the same everywhere — terminal, desktop app, or claude.ai/code.

2. Put your DataForSEO credentials in a `.env` file in that project:

   ```
   DATAFORSEO_LOGIN=your-api-login
   DATAFORSEO_PASSWORD=your-api-password
   ```

   Get them from <https://app.dataforseo.com/api-access>. Make sure `.env` is in
   your `.gitignore` — never commit it.

   **Create this file yourself, in your own editor or terminal.** Don't paste
   credentials into the chat and don't ask Claude to write the file for you —
   anything typed into a conversation becomes part of that conversation.

   The skill is built to keep it that way: it loads `.env` into the shell
   environment and only checks that the values are non-empty. It never reads the
   file's contents, never prints a credential, and its error messages name only
   where a credential came from, not what it is. If you ever see it about to
   display the file, stop it — that's not how it's meant to work.

   If your credentials live somewhere else — exported in your shell, or behind a
   proxy that injects them — that works too; the skill checks those first and
   tells you which one it used.

   **Using a dev container, WSL, or Remote-SSH?** Put the `.env` where the session
   actually runs — inside the container or on the remote host — not on the laptop
   you're typing on. A file sitting on the laptop is invisible to a session
   running somewhere else, and the usual symptom is the skill reporting no
   credentials when you can plainly see the file. `/aeo-faq --check` prints the
   exact paths it searched, which settles it quickly.

3. Check it before you spend anything:

   ```
   /aeo-faq --check
   ```

   That calls a free DataForSEO endpoint and reports whether your setup works. If
   it can't find credentials it will say so and stop, rather than half-running.

4. Optional — try the whole thing for free:

   ```
   /aeo-faq --sandbox https://your-site.com/a-page
   ```

   DataForSEO's sandbox returns placeholder values with the same structure as
   real results, and never charges. This runs the entire workflow end to end, so
   you can confirm everything works before spending a cent.

   The questions and answers it produces are meaningless — it's a plumbing test,
   not a preview. Output is labelled accordingly and the schema file is named so
   it can't be pasted onto a page by mistake.

## On Claude Code for the web

A cloud session doesn't run on your machine, so there's no "create the file
yourself" step — and the environment variables box in the environment settings
says outright not to put secrets in it, because everyone using that environment
can read them. Don't paste credentials into the chat either.

**On Pro and Max plans there's a proper answer.** Add an *API credential* to the
cloud environment and Anthropic's agent proxy attaches it to matching requests
*after they leave the session*. The key never reaches Claude, the commands it
runs, or the session's environment variables — the same idea as a secrets proxy.

In the **Update cloud environment** dialog at claude.ai/code, under **API
credentials**, select **Add credential**:

| Field | Value |
|---|---|
| Credential type | Bearer |
| Name | anything, e.g. `DataForSEO` |
| Allowed websites | `api.dataforseo.com` and `sandbox.dataforseo.com` |
| Custom header — Name | `Authorization` |
| Custom header — Prefix | `Basic` |
| Custom header — Value | base64 of `login:password` |

Generate that value on your own machine, never in the session:

```bash
printf '%s:%s' 'your-api-login' 'your-api-password' | base64 -w0
```

Then just run `/aeo-faq --check`. There's nothing to configure in the skill — it
notices that requests are being authenticated for it and says so.

**On Team and Enterprise plans this isn't available yet.** There is currently no
safe way to give a cloud session a third-party API key, so use Claude Code
locally, or the Python CLI, instead. Adding the key as an environment variable is
not a workaround — every member of that environment can read it.

Adding a credential needs an organization admin role, an Anthropic-hosted
environment, and an organization not using customer-managed encryption keys.

## Usage

```
/aeo-faq
/aeo-faq https://client.com/walk-in-tubs
/aeo-faq https://client.com/walk-in-tubs walk in tub cost, walk in tub installation
```

You don't need to know the queries up front. Whatever you leave out, the skill
asks for before spending anything: which page, which queries to target (it
suggests some from the page), any exact prompts you want covered (they go into
the FAQ first and cost nothing), which market, how many questions, and whether the
site has a general FAQ page (it suggests the one it found). It shows the cost and
waits for your go-ahead.

Before drafting, it reads the general FAQ and the related pages in the site's
sitemap, so it doesn't write a question the site already answers. Each question's
full answer goes on the page most centred on its topic. When that's another page,
this page gets a short, differently worded answer that links there, so the pages
support each other instead of competing. When this page is the better home, the
draft lists which other pages to shorten and link here.

It also reads the top-ranking competitor pages and suggests questions for the
concepts they cover and this page doesn't, since those pages are what "People
also ask" boxes and AI answers draw on.
Those are labelled as concept gaps, not Google data, and answered only from the
client's own material.

The draft opens with **What to do** — content to write, other pages to shorten,
already-answered questions — and the answers follow.

For many pages (a blog, a section), ask for it in plain words, e.g. "FAQs for
every post under /blog/". The skill lists the pages, suggests a query for each,
shows the estimated cost before spending, and writes a summary across all
pages. Claude usage grows with the number of pages; past about 10, the Python
CLI's `--plan` / `--batch` is cheaper.

You get `faq-draft.md` and `faq-schema.jsonld` written into the project, plus a
summary in chat: the page's primary search intent, how many questions were
answered here, linked to another page, or flagged as content gaps, which other
pages to shorten, and what DataForSEO charged.

Cost is one API call per keyword. Measured on live runs: **$0.0038 per keyword**,
so a typical one-to-three keyword run is around a cent. The run reports the amount
actually charged, read back from the API response.

## If your credentials are sensitive

Two honest caveats, so you can judge rather than trust.

The skill calls the API with `curl -u "$LOGIN:$PASSWORD"`. The values come from
shell variables, so they aren't written into any command Claude composes — but
while the request runs they are visible in the machine's process list to anyone
else logged into that machine. On your own laptop that's nobody. On a shared
server or build box, it isn't.

If that matters, use the Python CLI in this repo instead. It passes
credentials in-process through the HTTP library, so they never reach a command
line, a temporary file, or the process list. Same workflow, same output.

Either way, `--check` and `--sandbox` let you verify everything before a single
paid call.

## What `[SITE GAP]` means

A question people ask on Google that nothing on the site currently has material to
answer.
The skill will not invent an answer for it, and will not put it in the FAQ schema —
marking up a question the page doesn't visibly answer is a markup violation.

Instead it names who Google's AI Overview currently cites for that topic, and shows
the closest the page gets, labelled as related but not an answer. Those two things
together are the content recommendation: what's being asked, who owns the answer
today, and how far the page is from competing.

A page that scores mostly gaps isn't a failed run — that's the finding.
