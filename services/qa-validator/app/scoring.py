from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from rapidfuzz import fuzz

from app.config import get_settings
from app.normalizer import normalize_text


PRICE_RE = re.compile(r"(?<!\d)(?:\$?\d{1,3}(?:\.\d{2})?)(?!\d)")
MENU_HEADERS = [
    "cocktails", "beer", "wine", "spirits", "appetizers", "entrees",
    "happy hour", "drinks", "draft", "bottle", "whiskey", "bourbon",
    "vodka", "gin", "rum", "tequila", "mezcal", "scotch",
]
BOILERPLATE_TERMS = [
    "copyright", "all rights reserved", "privacy policy",
    "subscribe", "newsletter", "follow us", "instagram", "facebook",
    "our story", "about us", "book now", "reservation", "home page",
]
AGGREGATOR_TERMS = [
    "yelp", "tripadvisor", "doordash", "grubhub", "ubereats",
    "opentable", "postmates", "seamless", "restaurantguru", "zomato",
]
BEVERAGE_TERMS = [
    "beer", "wine", "cocktail", "cocktails", "spirits", "tequila",
    "whiskey", "whisky", "vodka", "gin", "rum", "bourbon", "scotch",
    "lager", "ipa", "stout", "sauvignon", "chardonnay", "cabernet",
    "pinot", "merlot", "mezcal", "brandy", "liqueur", "ale",
]


@dataclass
class ScoreBundle:
    freshness_score: float
    venue_accuracy_score: float
    menu_validity_score: float
    beverage_relevance_score: float
    composite_score: float
    failure_reason: str
    llm_used: bool = False
    debug_meta: dict[str, Any] = field(default_factory=dict)


def ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return fuzz.token_sort_ratio(a, b) / 100.0


def partial(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return fuzz.partial_ratio(a, b) / 100.0


def score_freshness(live_text: str, captured_text: str) -> float:
    live = normalize_text(live_text)[:15000]
    stored = normalize_text(captured_text)[:15000]
    return ratio(live, stored)


def score_venue_accuracy(name: str, address: str | None, city: str | None, live_text: str) -> tuple[float, dict]:
    text = normalize_text(live_text).lower()
    name_score = partial((name or "").lower(), text)
    address_score = partial((address or "").lower(), text) if address else 0.0
    city_score = partial((city or "").lower(), text) if city else 0.0

    secondary = max(address_score, city_score)
    combined = min(1.0, (name_score * 0.75) + (secondary * 0.25))

    return combined, {
        "name_score": round(name_score, 4),
        "address_score": round(address_score, 4),
        "city_score": round(city_score, 4),
    }


def score_menu_validity_rule(live_text: str) -> tuple[float, dict]:
    text = normalize_text(live_text).lower()

    price_count = len(PRICE_RE.findall(text))
    header_hits = sum(1 for h in MENU_HEADERS if h in text)
    boilerplate_hits = sum(1 for t in BOILERPLATE_TERMS if t in text)
    aggregator_hits = sum(1 for t in AGGREGATOR_TERMS if t in text)

    menu_line_like = 0
    for segment in text.split("."):
        seg = segment.strip()
        if not seg:
            continue
        has_price = bool(PRICE_RE.search(seg))
        token_len = len(seg.split())
        if has_price and 2 <= token_len <= 18:
            menu_line_like += 1

    score = 0.0
    score += min(price_count / 12.0, 1.0) * 0.35
    score += min(header_hits / 5.0, 1.0) * 0.30
    score += min(menu_line_like / 12.0, 1.0) * 0.35

    if boilerplate_hits >= 3:
        score -= 0.20
    if aggregator_hits >= 1:
        score -= 0.35

    score = max(0.0, min(score, 1.0))

    return score, {
        "price_count": price_count,
        "header_hits": header_hits,
        "boilerplate_hits": boilerplate_hits,
        "aggregator_hits": aggregator_hits,
        "menu_line_like": menu_line_like,
    }


def score_beverage_relevance(live_text: str) -> tuple[float, dict]:
    text = normalize_text(live_text).lower()
    hits = sum(1 for t in BEVERAGE_TERMS if t in text)
    price_hits = len(PRICE_RE.findall(text))
    score = min(hits / 8.0, 1.0) * 0.7 + min(price_hits / 8.0, 1.0) * 0.3
    score = min(score, 1.0)
    return score, {"beverage_term_hits": hits, "price_hits": price_hits}


def needs_llm(freshness_score: float, menu_validity_score: float) -> bool:
    settings = get_settings()
    return (
        settings.freshness_ambiguous_low <= freshness_score <= settings.freshness_ambiguous_high
        or settings.menu_validity_ambiguous_low <= menu_validity_score <= settings.menu_validity_ambiguous_high
    )


def compute_composite(
    freshness_score: float,
    venue_accuracy_score: float,
    menu_validity_score: float,
    beverage_relevance_score: float,
) -> float:
    weights = get_settings().composite_weights
    score = (
        menu_validity_score * weights["menu_validity"]
        + venue_accuracy_score * weights["venue_accuracy"]
        + freshness_score * weights["freshness"]
        + beverage_relevance_score * weights["beverage_relevance"]
    )
    return round(max(0.0, min(score, 1.0)), 4)


def choose_failure_reason(
    freshness_score: float,
    venue_accuracy_score: float,
    menu_validity_score: float,
    beverage_relevance_score: float,
    fetch_failed: bool,
    rule_meta: dict,
) -> str:
    if fetch_failed:
        return "fetch_failed"
    if rule_meta.get("aggregator_hits", 0) > 0 and menu_validity_score < 0.5:
        return "aggregator_page"
    if venue_accuracy_score < 0.45:
        return "wrong_venue"
    if menu_validity_score < 0.25 and rule_meta.get("boilerplate_hits", 0) >= 2:
        return "boilerplate_only"
    if menu_validity_score < 0.40:
        return "not_a_menu"
    if freshness_score < get_settings().freshness_pass_threshold:
        return "stale_content"
    if beverage_relevance_score < 0.35:
        return "no_beverage_content"
    return "pass"
