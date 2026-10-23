"""DataForSEO SERP client: fetch one Google SERP and parse what we need from it.

Request and response shapes follow DataForSEO's
/v3/serp/google/organic/live/advanced endpoint.

A note on People Also Ask, verified against live responses 2026-09-23: the
question text (``people_also_ask_element.title``) is reliable, but the expanded
answer is not. Google now commonly returns a
``people_also_ask_ai_overview_expanded_element`` carrying
``asynchronous_ai_overview: true`` with description, url, domain and references
all null, and passing ``load_async_ai_overview`` does not populate them. So we
take the questions from PAA and take citation provenance from the ``ai_overview``
block instead, which does arrive populated and is the more useful signal anyway:
it names the pages Google's AI cites for the topic.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from . import FaqgenError
from .credentials import Transport

ORGANIC_ENDPOINT = "/v3/serp/google/organic/live/advanced"

LOCATION_CODES = {
    "us": 2840,
    "canada": 2124,
    "uk": 2826,
    "australia": 2036,
    "france": 2250,
    "germany": 2276,
}

# Measured live 2026-09-23: depth 20 + people_also_ask_click_depth 2 charged
# $0.0038. The actual charge is always read back from the response `cost` field.
EST_COST_PER_KEYWORD = 0.004
PARTIAL_RESULTS = 40106


@dataclass
class Reference:
    """A page Google cites — from the AI Overview block."""
    domain: str = ""
    url: str = ""
    title: str = ""


@dataclass
class PAAQuestion:
    question: str
    rank: int                      # 1-based position within the PAA block
    answer_text: str = ""          # Google's expanded answer, when served at all
    source_url: str = ""
    source_domain: str = ""
    source_title: str = ""
    keyword: str = ""              # which query surfaced this question
    from_related: bool = False     # came from related_searches, not the PAA block
    from_prompt: bool = False      # an exact prompt the user asked to target
    prompt: str = ""               # the prompt as the user wrote it


@dataclass
class OrganicResult:
    url: str
    title: str
    description: str
    rank: int
    domain: str = ""


@dataclass
class SerpData:
    keyword: str
    paa: List[PAAQuestion] = field(default_factory=list)
    related_searches: List[str] = field(default_factory=list)
    organic: List[OrganicResult] = field(default_factory=list)
    ai_overview_refs: List[Reference] = field(default_factory=list)
    cost: float = 0.0
    raw: Optional[Dict[str, Any]] = None


class DataForSEOClient:
    """Talks to DataForSEO over whichever route the credential resolver found."""

    def __init__(self, transport: Transport):
        self.transport = transport
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        if transport.auth:
            self.session.auth = transport.auth

    def fetch_serp(
        self,
        keyword: str,
        location_code: int = 2840,
        language_code: str = "en",
        paa_depth: int = 2,
        depth: int = 20,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """One live advanced SERP call. Returns the raw API response."""
        task: Dict[str, Any] = {
            "keyword": keyword[:700],
            "location_code": location_code,
            "language_code": language_code,
            "device": "desktop",
            "depth": depth,
        }
        if paa_depth > 0:
            task["people_also_ask_click_depth"] = min(paa_depth, 4)

        url = self.transport.url_for(ORGANIC_ENDPOINT)
        try:
            response = self.session.post(url, json=[task], timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise FaqgenError(
                f"DataForSEO request failed ({self.transport.describe()}): {exc}"
            ) from exc

        data = response.json()
        status = data.get("status_code")
        if status != 20000:
            raise FaqgenError(
                f"DataForSEO error {status}: {data.get('status_message', 'unknown')}\n"
                f"Route in use: {self.transport.describe()}"
            )
        return data

    def check(self, timeout: int = 30) -> Dict[str, Any]:
        """Prove the route works, using an endpoint that costs nothing."""
        url = self.transport.url_for("/v3/appendix/user_data")
        try:
            response = self.session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (401, 403):
                source = self.transport.source or "your configuration"
                raise FaqgenError(
                    "DataForSEO rejected those credentials (401 Unauthorized).\n\n"
                    f"They were read from: {source}\n\n"
                    "The API login and password are NOT your dataforseo.com website\n"
                    "login. Copy them from the API Access tab at\n"
                    "https://app.dataforseo.com/api-access — the password there is a\n"
                    "generated string, not the one you sign in with.\n\n"
                    "Check too that the file has no quotes around the values and no\n"
                    "trailing spaces."
                ) from exc
            raise FaqgenError(
                f"DataForSEO returned HTTP {status} ({self.transport.describe()})."
            ) from exc
        except requests.RequestException as exc:
            raise FaqgenError(
                f"Couldn't reach DataForSEO ({self.transport.describe()}): "
                f"{exc.__class__.__name__}"
            ) from exc


def _extract_references(block: Dict[str, Any]) -> List[Reference]:
    out = []
    for ref in block.get("references") or []:
        if not isinstance(ref, dict):
            continue
        domain, url = ref.get("domain") or "", ref.get("url") or ""
        if domain or url:
            out.append(Reference(
                domain=domain, url=url, title=ref.get("title") or ""
            ))
    return out


def _parse_paa_element(element: Dict[str, Any], keyword: str, rank: int) -> Optional[PAAQuestion]:
    question = (element.get("title") or "").strip()
    if not question:
        return None
    paa = PAAQuestion(question=question, rank=rank, keyword=keyword)

    for expanded in element.get("expanded_element") or []:
        if not isinstance(expanded, dict):
            continue
        # Older shape: flat description/url/domain. Newer AI-overview shape
        # carries nulls here, in which case we simply keep the question.
        description = (expanded.get("description") or "").strip()
        if description and not paa.answer_text:
            paa.answer_text = description
            paa.source_url = expanded.get("url") or ""
            paa.source_domain = expanded.get("domain") or ""
            paa.source_title = expanded.get("title") or ""
        if not paa.source_domain:
            for ref in _extract_references(expanded):
                paa.source_url = paa.source_url or ref.url
                paa.source_domain = paa.source_domain or ref.domain
                paa.source_title = paa.source_title or ref.title
                break
    return paa


def parse_serp(raw: Dict[str, Any], keyword: str) -> SerpData:
    """Parse a raw SERP response into questions, organic results, AI citations."""
    serp = SerpData(keyword=keyword, raw=raw, cost=float(raw.get("cost") or 0.0))

    try:
        task = raw["tasks"][0]
        # 40106 = partial results: some SERP pages failed, the rest are usable
        # and only those were charged.
        if task.get("status_code") not in (None, 20000, PARTIAL_RESULTS):
            raise FaqgenError(
                f"DataForSEO task error {task.get('status_code')}: "
                f"{task.get('status_message', 'unknown')}"
            )
        items = (task["result"][0] or {}).get("items") or []
    except (KeyError, IndexError, TypeError):
        return serp

    for item in items:
        itype = item.get("type", "")

        if itype == "people_also_ask":
            for element in item.get("items") or []:
                if not isinstance(element, dict):
                    continue
                question = _parse_paa_element(
                    element, keyword, len(serp.paa) + 1
                )
                if question:
                    serp.paa.append(question)

        elif itype == "ai_overview":
            serp.ai_overview_refs.extend(_extract_references(item))

        elif itype == "related_searches":
            for rel in item.get("items") or []:
                text = rel if isinstance(rel, str) else (rel.get("title") or "")
                text = text.strip()
                if text:
                    serp.related_searches.append(text)

        elif itype == "organic" and len(serp.organic) < 10:
            serp.organic.append(OrganicResult(
                url=item.get("url") or "",
                title=item.get("title") or "",
                description=item.get("description") or "",
                rank=item.get("rank_group") or len(serp.organic) + 1,
                domain=item.get("domain") or "",
            ))

    return serp


_QUESTION_STARTERS = (
    "how", "what", "why", "when", "where", "which", "who", "whose",
    "can", "could", "do", "does", "did", "is", "are", "was", "were",
    "should", "will", "would",
)


def question_shaped_related(serp: SerpData) -> List[PAAQuestion]:
    """Related searches that read as questions — secondary FAQ candidates."""
    out = []
    for i, text in enumerate(serp.related_searches):
        first = text.split(" ", 1)[0].lower() if text else ""
        if first in _QUESTION_STARTERS:
            out.append(PAAQuestion(
                question=text if text.endswith("?") else f"{text.rstrip('?')}?",
                rank=len(serp.paa) + i + 1,
                keyword=serp.keyword,
                from_related=True,
            ))
    return out


def citation_provenance(serp: SerpData, limit: int = 3) -> List[Reference]:
    """Who Google currently cites for this topic — AI Overview first, then organic.

    Used only to annotate PAGE GAP flags: it shows whose content owns the answer
    the client's page cannot give. Never a source for answer text.
    """
    if serp.ai_overview_refs:
        return serp.ai_overview_refs[:limit]
    return [
        Reference(domain=o.domain, url=o.url, title=o.title)
        for o in serp.organic[:limit]
    ]


def estimate_cost(n_keywords: int, paa_depth: int = 2) -> float:
    return round(EST_COST_PER_KEYWORD * n_keywords, 4)


def prompt_questions(prompts: List[str]) -> List[PAAQuestion]:
    """The user's exact prompts, as FAQ candidates that outrank Google's.

    Prompts are written the way people talk to an AI assistant, so they are
    not looked up in Google: a long conversational prompt rarely has a PAA box
    and the call would be wasted. They go straight into the FAQ instead.
    """
    out = []
    for i, prompt in enumerate(p.strip() for p in prompts):
        if not prompt:
            continue
        question = prompt[0].upper() + prompt[1:]
        if not question.endswith("?"):
            question = question.rstrip(".!") + "?"
        out.append(PAAQuestion(
            question=question, rank=i + 1, keyword="your prompt",
            from_prompt=True, prompt=prompt,
        ))
    return out
