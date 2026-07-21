import json
import time

import requests

from swift_scanner import news_calendar


def test_fetch_calendar_events_uses_stale_cache_on_timeout(monkeypatch, tmp_path):
    cache_file = tmp_path / "news_calendar_cache.json"
    stale_payload = {
        "fetched_at": time.time() - 7200,
        "events": [
            {
                "date": "2026-07-20T14:00:00Z",
                "currency": "USD",
                "event": "Retail Sales",
                "impact": "High",
            }
        ],
    }
    cache_file.write_text(json.dumps(stale_payload), encoding="utf-8")

    monkeypatch.setattr(news_calendar, "CACHE_FILE", cache_file)

    def raise_timeout(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(news_calendar.requests, "get", raise_timeout)

    events = news_calendar.fetch_calendar_events()

    assert events == stale_payload["events"]


def test_fetch_calendar_events_retries_primary_and_uses_fallback_url(monkeypatch, tmp_path):
    cache_file = tmp_path / "news_calendar_cache.json"
    monkeypatch.setattr(news_calendar, "CACHE_FILE", cache_file)
    monkeypatch.setattr(news_calendar, "_get_calendar_urls", lambda: ["https://primary.example", "https://fallback.example"])
    monkeypatch.setattr(news_calendar.time, "sleep", lambda *_: None)

    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout, headers):
        calls.append(url)
        if url == "https://primary.example":
            raise requests.Timeout("timed out")
        return FakeResponse([
            {
                "date": "2026-07-20T14:00:00Z",
                "country": "USD",
                "title": "Retail Sales",
                "impact": "High",
            }
        ])

    monkeypatch.setattr(news_calendar.requests, "get", fake_get)

    events = news_calendar.fetch_calendar_events()

    assert len(events) == 1
    assert events[0]["event"] == "Retail Sales"
    assert calls == ["https://primary.example", "https://primary.example", "https://fallback.example"]
