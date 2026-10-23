"""Fetch a page and extract the text the FAQ answers must be grounded in."""

import html
import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import FaqgenError

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_SKIP_PARENTS = {"script", "style", "noscript", "template", "nav", "footer", "form"}
_MIN_PARAGRAPH_CHARS = 60
# Short text blocks (pricing cards, feature bullets in <div>s, "14-day refund
# guarantee" lines) hold answers too. A live pricing page read 103 of 264
# words before these were included, and a refund question was called
# unanswered. Kept separate from the paragraph rule so one-word labels stay out.
_MIN_BLOCK_CHARS = 20
_MIN_BLOCK_WORDS = 3
_BLOCK_TAGS = ["div", "span", "td", "th", "dd", "dt", "blockquote", "figcaption", "summary"]
_BLOCK_CHILDREN = ["p", "li", "div", "ul", "ol", "table", "h1", "h2", "h3", "h4"]
# Overlays and consent banners are not page content, whatever tag they use.
_SKIP_ROLES = {"dialog", "alertdialog", "navigation", "banner", "contentinfo"}
# Matched as whole tokens of class/id names ("cookie-banner" → cookie), never
# on <html>/<body>: a theme's body class list contained "modal" and emptied a
# whole page when this was a substring match on every ancestor.
_SKIP_TOKENS = {"cookie", "cookies", "consent", "gdpr", "cmplz", "popup", "modal",
                "overlay", "lightbox"}
_TOKEN_SPLIT = re.compile(r"[\s_\-:/.]+")


@dataclass
class PageContent:
    url: str
    title: str = ""
    h1: str = ""
    meta_description: str = ""
    headings: List[Tuple[str, str]] = field(default_factory=list)  # (tag, text)
    # (paragraph text, nearest preceding heading text)
    paragraphs: List[Tuple[str, str]] = field(default_factory=list)
    # (absolute href, anchor text) — nav and footer included, since that is
    # where a site usually links its general FAQ.
    links: List[Tuple[str, str]] = field(default_factory=list)
    # (question, answer) pairs the page already publishes, from FAQPage
    # JSON-LD, <details> accordions, or headings phrased as questions.
    qa_pairs: List[Tuple[str, str]] = field(default_factory=list)

    def derived_keyword(self) -> str:
        """Fallback target keyword from title/h1, brand suffix stripped."""
        base = self.h1 or self.title
        base = re.split(r"\s+[|–—-]\s+", base)[0]
        return base.strip().lower()

    def keyword_suggestions(self) -> List[str]:
        """Query ideas from the H1 and the title, likeliest first.

        An H1 that reads like a sentence ("A program built around your
        habits.") is a slogan, not a query, so the title goes first then.
        """
        def clean(text: str) -> str:
            return re.split(r"\s+[|–—-]\s+", text)[0].strip().rstrip(".!").lower()

        h1, title = clean(self.h1), clean(self.title)
        slogan = len(h1.split()) > 6 or "," in h1
        ordered = [title, h1] if slogan else [h1, title]
        return [s for i, s in enumerate(ordered) if s and s not in ordered[:i]]


def fetch_page(url: str, timeout: int = 15, user_agent: str = "") -> PageContent:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": user_agent or _UA},
            timeout=timeout,
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status in (401, 403, 429):
            raise FaqgenError(
                f"{url} refused an automated fetch ({status}).\n"
                "Some sites block anything that isn't a person in a browser. Options:\n"
                "  - Run this against a page you or your client controls.\n"
                "  - Set a user agent the site accepts: --user-agent 'YourTool/1.0 "
                "(+https://your-site.com/bot)'\n"
                "  - Save the page's HTML and open an issue for file input if you "
                "need this often."
            ) from exc
        raise FaqgenError(
            f"Could not fetch {url}: {exc}\n"
            "Check the URL is correct and publicly reachable."
        ) from exc
    except requests.RequestException as exc:
        raise FaqgenError(
            f"Could not fetch {url}: {exc}\n"
            "Check the URL is public and reachable from this machine."
        ) from exc

    page = parse_html(url, _decoded(response))
    if not page.paragraphs:
        raise FaqgenError(
            f"No readable text found on {url}. The page may render entirely "
            "with JavaScript — try a different URL, or paste the page content "
            "into the finish-in-Claude step manually."
        )
    return page


def fetch_html(url: str, timeout: int = 15, user_agent: str = "") -> Optional[str]:
    """Best-effort GET for site pages: returns None instead of raising."""
    try:
        response = requests.get(
            url, headers={"User-Agent": user_agent or _UA},
            timeout=timeout, allow_redirects=True,
        )
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    return _decoded(response)


def _decoded(response: requests.Response) -> str:
    """Response text in the right character set.

    With no charset in the Content-Type header, requests falls back to
    ISO-8859-1 for text/html, which turns UTF-8 apostrophes into "â".
    Let BeautifulSoup read the bytes instead: it honours the page's
    <meta charset> and sniffs the rest.
    """
    content_type = response.headers.get("Content-Type", "").lower()
    if "charset=" in content_type:
        return response.text
    if "html" not in content_type:
        # Sitemaps and robots.txt: XML declares its own encoding, UTF-8 by default.
        return response.content.decode("utf-8", errors="replace")
    return BeautifulSoup(response.content, "html.parser").decode()


def parse_html(url: str, html: str) -> PageContent:
    soup = BeautifulSoup(html, "html.parser")
    page = PageContent(url=url)

    if soup.title and soup.title.string:
        page.title = soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        page.h1 = h1.get_text(" ", strip=True)
    meta = soup.find("meta", attrs={"name": "description"})
    if meta:
        page.meta_description = (meta.get("content") or "").strip()

    current_heading = page.h1
    body = soup.body or soup
    seen = set()
    for el in body.find_all(["h1", "h2", "h3", "h4", "p", "li", *_BLOCK_TAGS]):
        if _skipped(el):
            continue
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name.startswith("h"):
            page.headings.append((el.name, text))
            current_heading = text
            continue
        # Only leaf blocks: a container's text would repeat its children, and a
        # layout <li> holding a whole column would swallow them into one blob.
        leaf = el.find(_BLOCK_CHILDREN) is None
        short_ok = len(text) >= _MIN_BLOCK_CHARS and len(text.split()) >= _MIN_BLOCK_WORDS
        if el.name in ("p", "li"):
            # A 56-character <p> ("Try it risk-free with our 14-day refund
            # guarantee.") is an answer; the old 60-character floor dropped it.
            keep = leaf and (len(text) >= _MIN_PARAGRAPH_CHARS or short_ok)
        else:
            keep = leaf and short_ok
        if keep and text not in seen:
            seen.add(text)
            page.paragraphs.append((text, current_heading))

    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"]).split("#")[0]
        if href.startswith("http"):
            page.links.append((href, a.get_text(" ", strip=True)))

    page.qa_pairs = _extract_qa_pairs(soup)
    return page


def _skipped(el) -> bool:
    """Inside navigation, a form, a button, an overlay or hidden markup."""
    for node in (el, *el.parents):
        name = getattr(node, "name", None)
        if name in (None, "html", "body", "[document]"):
            continue
        if name in _SKIP_PARENTS or name in ("a", "button"):
            return True
        attrs = getattr(node, "attrs", None) or {}
        if attrs.get("hidden") is not None or attrs.get("aria-hidden") == "true":
            return True
        if attrs.get("role") in _SKIP_ROLES:
            return True
        names = " ".join([*attrs.get("class", []), attrs.get("id", "") or ""]).lower()
        if _SKIP_TOKENS & set(_TOKEN_SPLIT.split(names)):
            return True
    return False


def _jsonld_faq_pairs(soup: BeautifulSoup) -> List[Tuple[str, str]]:
    pairs = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            if node.get("@type") == "Question" and node.get("name"):
                answer = node.get("acceptedAnswer") or {}
                if isinstance(answer, list):
                    answer = answer[0] if answer else {}
                text = answer.get("text", "") if isinstance(answer, dict) else ""
                # Names often carry entities ("BrandName&#x2122; Kit").
                pairs.append((
                    html.unescape(node["name"]).strip(),
                    BeautifulSoup(html.unescape(text), "html.parser").get_text(" ", strip=True),
                ))
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return pairs


def _extract_qa_pairs(soup: BeautifulSoup) -> List[Tuple[str, str]]:
    """Questions the page already answers, in whatever form it publishes them.

    Visible Q&A comes first, so when a question appears both on the page and in
    its FAQPage markup, the visible wording wins. The two can differ: one live
    page's markup said "23 days" where the page said "2-3 days".
    """
    pairs = []

    for details in soup.find_all("details"):
        summary = details.find("summary")
        if not summary:
            continue
        question = summary.get_text(" ", strip=True)
        answer = details.get_text(" ", strip=True)[len(question):].strip()
        if question.endswith("?") and answer:
            pairs.append((question, answer))

    # Headings phrased as questions, answered by the text that follows them.
    body = soup.body or soup
    question, answer_parts = None, []
    for el in body.find_all(["h2", "h3", "h4", "h5", "p", "li"]):
        if any(parent.name in _SKIP_PARENTS for parent in el.parents):
            continue
        text = el.get_text(" ", strip=True)
        if el.name.startswith("h"):
            if question and answer_parts:
                pairs.append((question, " ".join(answer_parts)))
            question = text if text.endswith("?") else None
            answer_parts = []
        elif question and text:
            answer_parts.append(text)
    if question and answer_parts:
        pairs.append((question, " ".join(answer_parts)))
    pairs += _jsonld_faq_pairs(soup)

    seen, unique = set(), []
    for q, a in pairs:
        key = re.sub(r"[^a-z0-9 ]", "", q.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            unique.append((q, a))
    return unique
