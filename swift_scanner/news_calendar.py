"""
swift_scanner/news_calendar.py

Fetches upcoming high-impact economic calendar events and formats them for
SwiftWatcher.upcoming_news_events, which is read by _check_news_blackout()
in watcher.py.

Uses a free, no-API-key economic calendar source. If you have an API key
for Finnhub/TradingEconomics/ForexFactory, swap fetch_calendar_events()
for that provider — the rest of the pipeline (caching, symbol mapping)
stays the same.

This file is additive: it does not modify watcher.py's logic, only
provides the data that the existing blackout hook expects.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

CACHE_FILE = Path("signals") / "news_calendar_cache.json"
CACHE_TTL_SECONDS = 3600  # refresh hourly
DEFAULT_CALENDAR_URLS = (
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://api.allorigins.win/raw?url=https://nfs.faireconomy.media/ff_calendar_thisweek.json",
)
RETRY_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 1.0

# Maps news event currencies/keywords to the symbols they affect in your watchlist
CURRENCY_SYMBOL_MAP: dict[str, list[str]] = {
    "USD": ["XAUUSD", "BTCUSD", "ETHUSD", "USOIL", "NASDAQ", "US30",
            "GBPUSD", "USDJPY", "EURUSD", "AUDUSD"],
    "EUR": ["EURUSD"],
    "GBP": ["GBPUSD"],
    "JPY": ["USDJPY"],
    "AUD": ["AUDUSD"],
    "XAU": ["XAUUSD"],
    "OIL": ["USOIL"],
}

HIGH_IMPACT_KEYWORDS = [
    "non-farm", "nonfarm", "nfp", "cpi", "interest rate", "fomc", "fed",
    "gdp", "unemployment", "rate decision", "boe", "boj", "ecb",
    "retail sales", "ppi", "pce",
]


def _load_cache(allow_stale: bool = False) -> dict | None:
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        age_seconds = time.time() - payload.get("fetched_at", 0)
        if age_seconds > CACHE_TTL_SECONDS and not allow_stale:
            return None
        return payload
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(events: list[dict]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": time.time(), "events": events}
    CACHE_FILE.write_text(json.dumps(payload, default=str), encoding="utf-8")


def _get_calendar_urls() -> list[str]:
    configured_urls = os.getenv("NEWS_CALENDAR_URLS")
    if configured_urls:
        return [url.strip() for url in configured_urls.split(",") if url.strip()]
    return list(DEFAULT_CALENDAR_URLS)


def _parse_calendar_events(raw_events: list[dict]) -> list[dict]:
    events: list[dict] = []
    for e in raw_events:
        impact = str(e.get("impact", "")).lower()
        if impact not in ("high",):
            continue
        events.append({
            "date": e.get("date"),
            "currency": e.get("country", ""),
            "event": e.get("title", ""),
            "impact": e.get("impact", ""),
        })
    return events


def fetch_calendar_events() -> list[dict]:
    """Fetch upcoming economic calendar events.

    Uses a free public calendar JSON feed. If this endpoint becomes
    unavailable, swap in your preferred provider (Finnhub /calendar/economic,
    TradingEconomics API, etc.) — just return a list of dicts with keys:
    'date' (ISO string), 'currency', 'event', 'impact' ('High'/'Medium'/'Low').
    """
    cached = _load_cache()
    if cached:
        return cached["events"]

    events: list[dict] = []
    last_error: Exception | None = None
    for url in _get_calendar_urls():
        for attempt in range(RETRY_ATTEMPTS):
            try:
                resp = requests.get(
                    url,
                    timeout=10,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                resp.raise_for_status()
                raw_events = resp.json()
                if not isinstance(raw_events, list):
                    raise ValueError("calendar payload was not a list")
                events = _parse_calendar_events(raw_events)
                _save_cache(events)
                return events
            except Exception as exc:
                last_error = exc
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                break

    stale_cached = _load_cache(allow_stale=True)
    if stale_cached and stale_cached.get("events"):
        print(f"[NewsCalendar] Failed to fetch calendar: {last_error}. Using stale cached events instead.")
        return stale_cached["events"]
    print(f"[NewsCalendar] Failed to fetch calendar: {last_error}. Blackout check will pass-through.")
    return []


def build_upcoming_news_events() -> list[tuple[datetime, list[str], dict[str, str]]]:
    """Convert raw calendar events into the format expected by SwiftWatcher.

    Each item is a tuple of (datetime_utc, affected_symbols, metadata).
    Metadata contains at least the event title, currency, and impact.
    """
    raw_events = fetch_calendar_events()
    result: list[tuple[datetime, list[str], dict[str, str]]] = []

    for e in raw_events:
        try:
            event_time = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
        except (KeyError, ValueError, AttributeError):
            continue

        currency = (e.get("currency") or "").upper()
        affected = CURRENCY_SYMBOL_MAP.get(currency, [])
        title = str(e.get("event", "")).strip()
        if not affected:
            title_lower = title.lower()
            if any(kw in title_lower for kw in HIGH_IMPACT_KEYWORDS):
                affected = CURRENCY_SYMBOL_MAP.get("USD", [])

        if affected:
            metadata = {
                "currency": currency,
                "event": title,
                "impact": str(e.get("impact", "")).title(),
            }
            result.append((event_time, affected, metadata))

    return result


def refresh_watcher_news_events(watcher) -> None:
    """Call this periodically (e.g. once per hour) in your main loop to keep
    watcher.upcoming_news_events current.

    Example integration in watcher.py run() loop:
        from swift_scanner.news_calendar import refresh_watcher_news_events
        refresh_watcher_news_events(self)
    """
    try:
        watcher.upcoming_news_events = build_upcoming_news_events()
        print(f"[NewsCalendar] Loaded {len(watcher.upcoming_news_events)} upcoming high-impact events.")
    except Exception as exc:
        print(f"[NewsCalendar] Refresh failed: {exc}")
        watcher.upcoming_news_events = []
