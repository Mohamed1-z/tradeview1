"""Replay a recent alert event through SwiftWatcher.handle_event() with a mocked MT5 client.

Usage:
    python tools/replay_event.py [EVENT_ID_OR_SYMBOL]

If an argument matches an `event_id` exactly it will use that, otherwise it will use the most
recent event whose `symbol_request` or `symbol_actual` contains the provided string.

This script is diagnostic only and uses a mocked MT5 client that does not connect to a real MT5 terminal.
"""
from __future__ import annotations
import sys
import json
from pathlib import Path
from swift_scanner.models import SignalEvent
from swift_scanner.watcher import SwiftWatcher
from swift_scanner.config import ScannerConfig
from types import SimpleNamespace


class MockTick(SimpleNamespace):
    pass


class MockMT5Client:
    def __init__(self, event: SignalEvent):
        self.event = event

    # Methods used by the watcher
    def get_symbol_market_status(self, symbol: str):
        # For indices like US30, use point=1 and digits=0
        return {
            "symbol": symbol,
            "visible": True,
            "trade_allowed": True,
            "tick_size": 1.0,
            "point": 1.0,
            "spread": 1,
            "digits": 0,
            "volume_step": 0.01,
            "volume_min": 0.01,
            "volume_max": 100.0,
        }

    def get_tick(self, symbol: str):
        # Use event.entry_price or event.price to build a bid/ask
        entry = float(self.event.entry_price or self.event.price)
        # For bearish trades, set bid slightly below entry so entry condition is met
        if self.event.zone.direction == "bearish":
            bid = entry - 1.0
            ask = entry - 0.5
        else:
            ask = entry + 1.0
            bid = entry + 0.5
        return MockTick(ask=ask, bid=bid)

    def has_open_position(self, symbol: str) -> bool:
        return False

    def is_symbol_tradeable(self, symbol: str) -> bool:
        return True

    def normalize_order_volume(self, symbol: str, volume: float) -> float:
        return float(volume)

    def place_market_order(self, symbol: str, direction: str, volume: float, stop_loss=None, take_profit=None, deviation=20, magic=None):
        print(f"[MockMT5] place_market_order called: {symbol} {direction} vol={volume} sl={stop_loss} tp={take_profit}")
        return 999999

    def get_open_positions(self, symbol: str):
        return []

    def get_recent_candles(self, symbol: str, timeframe_name: str, count: int = 5):
        # Return simple synthetic candles aligned with the event direction
        base = float(self.event.entry_price or self.event.price)
        candles = []
        for i in range(count):
            if self.event.zone.direction == "bullish":
                open_p = base - (count - i)
                close_p = base - (count - i) + 1
            else:
                open_p = base + (count - i)
                close_p = base + (count - i) - 1
            candles.append({"open": open_p, "high": max(open_p, close_p) + 0.5, "low": min(open_p, close_p) - 0.5, "close": close_p, "volume": 100})
        return candles


def load_events(history_path: Path):
    events = []
    with history_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                payload = json.loads(line)
                events.append(payload)
            except Exception:
                continue
    return events


def find_event(payloads, selector: str | None):
    if not selector:
        # pick last US30-like event
        for p in reversed(payloads):
            sr = p.get("symbol_request", "")
            if "US30" in sr or "US30" in str(p.get("symbol_actual", "")):
                return p
        return payloads[-1] if payloads else None
    # exact match on event_id
    for p in reversed(payloads):
        if p.get("event_id") == selector:
            return p
    # substring match on symbol
    for p in reversed(payloads):
        if selector.upper() in p.get("symbol_request", "").upper() or selector.upper() in str(p.get("symbol_actual", "")).upper():
            return p
    return None


def main():
    selector = sys.argv[1] if len(sys.argv) > 1 else None
    history = Path("signals/alert_history.jsonl")
    if not history.exists():
        print("No signals/alert_history.jsonl file found in workspace.")
        return
    payloads = load_events(history)
    if not payloads:
        print("No events found in alert_history.jsonl")
        return
    payload = find_event(payloads, selector)
    if not payload:
        print("No matching event found for selector.")
        return
    event = SignalEvent.from_dict(payload)
    print(f"Replaying event: {event.event_id}\n  label={event.signal_label} direction={event.zone.direction} entry={event.entry_price or event.price} stop={event.stop_loss} tp1={event.take_profit_1}")

    cfg = ScannerConfig()
    watcher = SwiftWatcher(cfg)
    # Ensure we don't treat the replayed event as already delivered
    try:
        watcher.sent_alert_ids.clear()
    except Exception:
        watcher.sent_alert_ids = set()
    mock = MockMT5Client(event)
    # Call handle_event directly — it will print its diagnostic flow to stdout
    watcher.handle_event(None, event, mock)


if __name__ == "__main__":
    main()
