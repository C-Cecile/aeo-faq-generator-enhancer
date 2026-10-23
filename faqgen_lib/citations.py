"""AI citation data: which pages AI assistants cite, for which prompts.

Reads exports from AI visibility tools so the FAQ can build on what assistants
already do:

- **Profound** JSON exports as they come: the citations export
  (``{"filters": ..., "data": [{"type": "citation", "url", "prompt", "topic",
  "tags": "Branded"|"Unbranded", "text", "platform", "date", ...}]}``) and the
  query fan-out export (``{"data": [{"prompt", "fanout_query",
  "fanout_count", "fanout_share"}]}``).
- **Any tool's CSV** with a URL column and a prompt column; other columns are
  matched by name when present (see ``_ALIASES``).

Counts come from sampled assistant runs, so they indicate tendencies, not
traffic; every figure used in a draft carries its source and date range.
"""

import csv
import json
import re
from functools import lru_cache
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from .drafting import tokenize

# A Profound export has ~40,000 citation rows but under 100 distinct prompts;
# tokenizing per row made each page's placement pass seconds long on a batch.
_prompt_tokens = lru_cache(maxsize=None)(tokenize)

# Header names, lower-cased, that mean the same thing across tools.
_ALIASES = {
    "url": ("url", "page", "page_url", "cited_url", "citation_url", "source_url", "link"),
    "prompt": ("prompt", "query", "question", "prompt_text"),
    "branded": ("tags", "tag", "branded", "prompt_type", "type_of_prompt", "brand_mention"),
    "topic": ("topic", "category", "prompt_topic", "cluster"),
    "text": ("text", "snippet", "quote", "excerpt", "cited_text"),
    "platform": ("platform", "engine", "model", "assistant"),
    "date": ("date", "run_date", "timestamp"),
    "type": ("type", "row_type"),
    "fanout_query": ("fanout_query", "fan_out_query", "sub_query", "search_query"),
    "fanout_share": ("fanout_share", "share"),
}
# Files whose names say they hold citation or fan-out data.
_NAME_HINT = re.compile(r"citation|fan.?out", re.I)
# A prompt "is about" a question when it shares this much of the question.
_PROMPT_MATCH = 0.5
# Below this many matching citations, the data says too little to decide.
MIN_EVIDENCE = 5


@dataclass
class Citation:
    url: str
    prompt: str
    branded: Optional[bool] = None       # None = the export doesn't say
    topic: str = ""
    text: str = ""
    platform: str = ""
    date: str = ""


@dataclass
class Fanout:
    prompt: str
    query: str
    share: float = 0.0


@dataclass
class CitationData:
    citations: List[Citation] = field(default_factory=list)
    fanouts: List[Fanout] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)   # file names read

    def __bool__(self) -> bool:
        return bool(self.citations or self.fanouts)

    def describe(self) -> str:
        dates = sorted(c.date[:10] for c in self.citations if c.date)
        span = f", {dates[0]} to {dates[-1]}" if dates else ""
        return (f"{len(self.citations)} citations and {len(self.fanouts)} fan-out "
                f"searches from {', '.join(self.sources)}{span}")

    # ── Questions about one site ───────────────────────────────────────────

    def own(self, site_url: str, unbranded_only: bool = True) -> List[Citation]:
        host = _host(site_url)
        return [c for c in self.citations
                if _host(c.url) == host and not (unbranded_only and c.branded)]

    def prompt_runs(self, prompt: str) -> int:
        """How many citation rows exist for a prompt (a proxy for answers)."""
        return sum(1 for c in self.citations if c.prompt == prompt)

    def _own_index(self, site_url: str):
        """(prompt tokens, page key) for the site's unbranded citations, built
        once per site: the full export is scanned once, not once per question."""
        host = _host(site_url)
        cache = self.__dict__.setdefault("_own_cache", {})
        if host not in cache:
            cache[host] = [(_prompt_tokens(c.prompt), _page_key(c.url))
                           for c in self.own(site_url)]
        return cache[host]

    def cited_pages(self, question: str, site_url: str) -> Counter:
        """Unbranded citations of the site's pages on prompts about this question."""
        q = tokenize(question)
        counts = Counter()
        if not q:
            return counts
        for prompt_tokens, page in self._own_index(site_url):
            if len(q & prompt_tokens) / len(q) >= _PROMPT_MATCH:
                counts[page] += 1
        return counts

    def weak_prompts(self, site_url: str, topic_terms: Sequence[str],
                     limit: int = 5) -> List[Tuple[str, float, str]]:
        """Unbranded prompts on this page's topic where the site is rarely cited.

        Returns (prompt, share of that prompt's citations that are the
        site's, the export's topic label), best fit first among the weakest.
        Suggestions for the user to pick from, never added automatically:
        for a single-subject brand most prompts share some words with the page.
        """
        topic = set()
        for term in topic_terms:
            topic |= tokenize(term)
        host = _host(site_url)
        totals, own, labels = Counter(), Counter(), {}
        for c in self.citations:
            if c.branded:
                continue
            totals[c.prompt] += 1
            labels.setdefault(c.prompt, c.topic)
            if _host(c.url) == host:
                own[c.prompt] += 1
        scored = []
        for prompt, total in totals.items():
            words = _prompt_tokens(prompt)
            shared = words & topic
            if total < MIN_EVIDENCE or len(shared) < 2:
                continue
            fit = len(shared) / len(words)
            scored.append((round(own[prompt] / total, 2), -fit, -total, prompt))
        scored.sort()
        return [(prompt, rate, labels.get(prompt, "")) for rate, _, _, prompt in scored[:limit]]

    def fanout_queries(self, prompts: Iterable[str], limit: int = 5) -> List[str]:
        """The searches assistants ran most for these prompts."""
        wanted = set(prompts)
        best: Dict[str, float] = {}
        for f in self.fanouts:
            if f.prompt in wanted and f.query.strip().rstrip("?") not in wanted:
                key = f.query.strip().lower()
                best[key] = max(best.get(key, 0.0), f.share)
        return [q for q, _ in sorted(best.items(), key=lambda kv: -kv[1])][:limit]


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _page_key(url: str) -> str:
    """Scheme-less URL without query or #:~:text= fragments."""
    parsed = urlparse(url)
    return f"{parsed.netloc.lower().removeprefix('www.')}{parsed.path.rstrip('/') or '/'}"


def page_key(url: str) -> str:
    return _page_key(url)


def _branded(value) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return None
    if "unbranded" in text or "non-branded" in text or text in ("false", "no", "0"):
        return False
    if "branded" in text or text in ("true", "yes", "1"):
        return True
    return None


def _pick(record: dict, name: str) -> str:
    # "Cited URL", "cited-url" and "cited_url" are the same column.
    lowered = {re.sub(r"[\s\-]+", "_", str(k).strip().lower()): v
               for k, v in record.items()}
    for alias in _ALIASES[name]:
        if alias in lowered and lowered[alias] not in (None, ""):
            return str(lowered[alias])
    return ""


def _from_records(records: Iterable[dict], data: CitationData) -> int:
    added = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        fanout = _pick(record, "fanout_query")
        prompt = _pick(record, "prompt")
        if fanout and prompt:
            try:
                share = float(_pick(record, "fanout_share") or 0)
            except ValueError:
                share = 0.0
            data.fanouts.append(Fanout(prompt=prompt, query=fanout, share=share))
            added += 1
            continue
        url = _pick(record, "url")
        if not (url and prompt):
            continue
        row_type = _pick(record, "type").lower()
        if row_type and row_type != "citation":
            continue        # e.g. Profound's raw web_search_result rows
        data.citations.append(Citation(
            url=url, prompt=prompt, branded=_branded(_pick(record, "branded")),
            topic=_pick(record, "topic"), text=_pick(record, "text"),
            platform=_pick(record, "platform"), date=_pick(record, "date"),
        ))
        added += 1
    return added


def load_file(path: Path, data: Optional[CitationData] = None) -> CitationData:
    data = data if data is not None else CitationData()
    path = Path(path)
    added = 0
    if path.suffix.lower() == ".json":
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return data
        records = raw.get("data", raw) if isinstance(raw, dict) else raw
        added = _from_records(records if isinstance(records, list) else [], data)
    elif path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as fh:
            added = _from_records(csv.DictReader(fh), data)
    if added:
        data.sources.append(path.name)
    return data


def discover(paths: Sequence[Path] = (), search_dirs: Sequence[Path] = ()) -> List[Path]:
    """Citation files: those named explicitly (files or folders), else files in
    ``search_dirs`` whose names mention citations or fan-outs, plus everything
    in a ``citations/`` folder there."""
    found: List[Path] = []

    def add_dir(d: Path, hinted_only: bool):
        for f in sorted(d.iterdir()):
            if f.suffix.lower() in (".json", ".csv") and f.is_file():
                if not hinted_only or _NAME_HINT.search(f.name):
                    found.append(f)

    for p in map(Path, paths):
        if p.is_dir():
            add_dir(p, hinted_only=False)
        elif p.is_file():
            found.append(p)
    if not paths:
        for d in map(Path, search_dirs):
            if (d / "citations").is_dir():
                add_dir(d / "citations", hinted_only=False)
            if d.is_dir():
                add_dir(d, hinted_only=True)
    return list(dict.fromkeys(found))


def load(paths: Sequence[Path]) -> CitationData:
    data = CitationData()
    for p in paths:
        load_file(p, data)
    return data
