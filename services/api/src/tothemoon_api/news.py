from __future__ import annotations

import hashlib
import re

from .models import Horizon, NewsCategory, NewsIngestRequest, NewsItem

_news_store: list[NewsItem] = []
_seen_hashes: set[str] = set()


def _generate_headline_hash(headline: str) -> str:
    normalized = re.sub(r"[^\w\s]", "", headline.lower()).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()


def classify_news(request: NewsIngestRequest) -> NewsItem:
    content = f"{request.headline.lower()} {(request.body or '').lower()}"

    category: NewsCategory = "general"
    if any(
        keyword in content
        for keyword in [
            "sec",
            "fca",
            "cftc",
            "regulation",
            "ban",
            "lawsuit",
            "court",
            "etf approval",
        ]
    ):
        category = "regulatory"
    elif any(
        keyword in content
        for keyword in ["cpi", "fed", "inflation", "interest rate", "powell", "macro"]
    ):
        category = "macro"
    elif any(
        keyword in content
        for keyword in ["binance", "coinbase", "ftx", "exchange", "delisting", "maintenance"]
    ):
        category = "exchange-specific"
    elif any(
        keyword in content
        for keyword in [
            "bitcoin",
            "btc",
            "ethereum",
            "eth",
            "upgrade",
            "fork",
            "hack",
            "smart contract",
        ]
    ):
        category = "asset-specific"

    horizon: Horizon = "short"
    if category in {"regulatory", "macro"}:
        horizon = "long"
        if "rate" in content or "cpi" in content:
            horizon = "medium"
    elif "upgrade" in content or "fork" in content:
        horizon = "medium"
    elif any(keyword in content for keyword in ["hack", "maintenance", "delisting"]):
        horizon = "short"

    impact_score = 5
    if category in {"regulatory", "macro"} or "hack" in content:
        impact_score = 8

    return NewsItem(
        id=_generate_headline_hash(request.headline),
        headline=request.headline,
        timestamp=request.timestamp,
        horizon=horizon,
        category=category,
        impact_score=impact_score,
        justification=f"Classified as {category}/{horizon} due to keyword match in the payload.",
    )


def ingest_news(request: NewsIngestRequest) -> NewsItem | None:
    news_item = classify_news(request)
    if news_item.id in _seen_hashes:
        return None

    _seen_hashes.add(news_item.id)
    _news_store.append(news_item)

    if len(_news_store) > 1000:
        dropped = _news_store.pop(0)
        _seen_hashes.discard(dropped.id)

    return news_item


def get_recent_news(
    *,
    limit: int = 50,
    horizon: Horizon | None = None,
    category: NewsCategory | None = None,
) -> list[NewsItem]:
    results = sorted(_news_store, key=lambda item: item.timestamp, reverse=True)
    if horizon:
        results = [item for item in results if item.horizon == horizon]
    if category:
        results = [item for item in results if item.category == category]
    return results[:limit]


def check_news_risk_filter(symbol: str) -> dict[str, object]:
    recent_news = get_recent_news(limit=10)
    risk_level = "low"
    reasons: list[str] = []

    for item in recent_news:
        if item.impact_score >= 8:
            risk_level = "high"
            reasons.append(f"High impact {item.category} news affecting {symbol}: {item.headline}")
        elif item.category == "exchange-specific" and "maintenance" in item.headline.lower():
            risk_level = "high"
            reasons.append(f"Exchange maintenance risk affecting {symbol}: {item.headline}")

    return {
        "symbol": symbol,
        "risk_level": risk_level,
        "is_trading_blocked": risk_level == "high",
        "reasons": reasons,
    }


def clear_news_store() -> None:
    _news_store.clear()
    _seen_hashes.clear()
