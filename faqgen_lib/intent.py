"""Primary search intent from keyword modifiers plus SERP composition.

The SERP-composition classifier is a slimmed copy of the seo-audit suite's
serp_validator patterns (URL matches weighted 2x over title/description).
"""

import re
from collections import defaultdict
from typing import Dict, List, Tuple

from .serp import OrganicResult

SERP_PAGE_TYPE_PATTERNS: Dict[str, List[Tuple[str, float]]] = {
    "blog": [
        (r"/blog/", 2.0),
        (r"/(?:article|post|news|insights|guide)/", 2.0),
        (r"\b(?:guide|what is|how to|explained|introduction|overview|tips|tutorial)\b", 1.0),
    ],
    "product": [
        (r"/(?:product|solution|feature|platform|service)/", 2.0),
        (r"\b(?:buy|shop|product|pricing|plans|free trial)\b", 1.0),
    ],
    "comparison": [
        (r"/(?:compare|vs|versus|comparison|alternative)/", 2.0),
        (r"\b(?:vs\.?|versus|comparison|compare|alternative|top \d+|best)\b", 1.0),
    ],
    "directory": [
        (r"/(?:directory|listing|list|catalog|marketplace)/", 2.0),
        (r"\b(?:directory|listing|top \d+ (?:best|tools|software))\b", 1.0),
    ],
    "ecommerce": [
        (r"(?:amazon\.com|ebay\.com|etsy\.com)", 2.0),
        (r"/(?:shop|store|buy|cart|checkout|collections)/", 2.0),
    ],
    "forum": [
        (r"(?:reddit\.com|stackexchange\.com|quora\.com)", 2.0),
        (r"/(?:forum|community|discuss|thread)/", 2.0),
    ],
    "video": [
        (r"(?:youtube\.com|vimeo\.com|youtu\.be)", 2.0),
    ],
}

PAGE_TYPE_INTENT = {
    "blog": "informational",
    "video": "informational",
    "forum": "informational",
    "comparison": "commercial",
    "directory": "commercial",
    "product": "transactional",
    "ecommerce": "transactional",
}

_KEYWORD_MODIFIERS = [
    ("transactional", r"\b(?:buy|price|pricing|cost of|order|coupon|discount|near me|for sale)\b"),
    ("commercial", r"\b(?:best|top|vs\.?|versus|review|reviews|compare|comparison|alternative)\b"),
    ("informational", r"^(?:how|what|why|when|where|which|who)\b|\b(?:guide|tutorial|tips|meaning|examples?)\b"),
]


def _classify_result(result: OrganicResult) -> str:
    scores: Dict[str, float] = defaultdict(float)
    text = f"{result.title} {result.description}".lower()
    url = result.url.lower()
    for page_type, patterns in SERP_PAGE_TYPE_PATTERNS.items():
        for pattern, weight in patterns:
            if re.search(pattern, url):
                scores[page_type] += weight * 2
            elif re.search(pattern, text):
                scores[page_type] += weight
    return max(scores, key=scores.get) if scores else "other"


def determine_intent(
    keywords: List[str], organic: List[OrganicResult]
) -> Tuple[str, str]:
    """Return (intent, one-line rationale)."""
    modifier_votes: Dict[str, int] = defaultdict(int)
    for kw in keywords:
        for intent, pattern in _KEYWORD_MODIFIERS:
            if re.search(pattern, kw.lower()):
                modifier_votes[intent] += 1
                break

    type_counts: Dict[str, int] = defaultdict(int)
    for result in organic:
        type_counts[_classify_result(result)] += 1
    serp_votes: Dict[str, int] = defaultdict(int)
    for page_type, count in type_counts.items():
        intent = PAGE_TYPE_INTENT.get(page_type)
        if intent:
            serp_votes[intent] += count

    combined: Dict[str, float] = defaultdict(float)
    for intent, votes in modifier_votes.items():
        combined[intent] += votes * 2.0   # explicit modifiers are strong signal
    for intent, votes in serp_votes.items():
        combined[intent] += votes

    if not combined:
        return "informational", "no strong modifier or SERP signal; defaulting to informational"

    winner = max(combined, key=combined.get)
    dominant_types = ", ".join(
        f"{t} x{c}" for t, c in sorted(type_counts.items(), key=lambda x: -x[1])
        if t != "other"
    ) or "mixed"
    parts = []
    if modifier_votes.get(winner):
        parts.append("keyword modifiers")
    if serp_votes.get(winner):
        parts.append(f"SERP composition ({dominant_types})")
    return winner, f"based on {' + '.join(parts) or 'weak signals'}"
