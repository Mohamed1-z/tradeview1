from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

from swift_scanner.config import ScannerConfig
from swift_scanner.models import SignalEvent, Zone
from swift_scanner.watcher import SwiftWatcher


def make_event(symbol: str, timeframe: str, direction: str) -> SignalEvent:
    zone = Zone(
        zone_type="fair_value_gap",
        direction=direction,
        symbol_request=symbol,
        symbol_actual=symbol,
        created_at=datetime.now(timezone.utc),
        left_index=0,
        right_index=1,
        top=1.01,
        bottom=0.99,
        formed_index=1,
    )
    return SignalEvent(
        symbol_request=symbol,
        symbol_actual=symbol,
        timeframe=timeframe,
        triggered_at=datetime.now(timezone.utc),
        price=1.0,
        zone=zone,
        signal_label="FVG Entry",
        entry_price=1.0,
        stop_loss=0.98,
        take_profit_1=1.02,
    )


def test_fvg_blocked_when_htf_mismatch(tmp_path):
    cfg = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(cfg)
    event = make_event("EURUSD", "M15", "bullish")

    mt5 = Mock()
    # Simulate parent HTF bias being bearish, which should block a bullish FVG
    watcher._get_htf_bias = Mock(return_value="bearish")

    allowed = watcher._check_zone_gating(event, mt5)

    assert allowed is False
    assert event.decision_action.upper() == "AVOID"


def test_fvg_allowed_when_htf_matches(tmp_path):
    cfg = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(cfg)
    event = make_event("EURUSD", "M15", "bullish")

    mt5 = Mock()
    watcher._get_htf_bias = Mock(return_value="bullish")

    allowed = watcher._check_zone_gating(event, mt5)

    assert allowed is True


def test_discount_zone_requires_fvg_confluence(tmp_path):
    cfg = ScannerConfig(output_dir=tmp_path)
    # ensure discount zone gating is configured to require FVG
    cfg.zone_gating["discount_zone"] = cfg.zone_gating["discount_zone"].__class__(
        entry_timeframes=("M15", "H1"),
        require_htf_bias=True,
        max_freshness_closes=3,
        allow_opposite_entry=False,
        min_signal_quality_score=50,
        require_fvg_confluence=True,
        premium_discount_fvg_bonus=7.0,
    )
    watcher = SwiftWatcher(cfg)
    event = make_event("EURUSD", "M15", "bullish")
    event.zone.zone_type = "discount_zone"
    event.signal_tags = ["discount_zone"]

    mt5 = Mock()
    watcher._get_htf_bias = Mock(return_value="bullish")

    allowed = watcher._check_zone_gating(event, mt5)

    assert allowed is False
    assert event.decision_action.upper() == "AVOID"


def test_discount_zone_allows_with_fvg_confluence(tmp_path):
    cfg = ScannerConfig(output_dir=tmp_path)
    cfg.zone_gating["discount_zone"] = cfg.zone_gating["discount_zone"].__class__(
        entry_timeframes=("M15", "H1"),
        require_htf_bias=True,
        max_freshness_closes=3,
        allow_opposite_entry=False,
        min_signal_quality_score=50,
        require_fvg_confluence=True,
        premium_discount_fvg_bonus=7.0,
    )
    watcher = SwiftWatcher(cfg)
    event = make_event("EURUSD", "M15", "bullish")
    event.zone.zone_type = "discount_zone"
    event.zone.metadata["fvg_confluence"] = True
    event.signal_tags = ["discount_zone", "fair_value_gap"]

    mt5 = Mock()
    watcher._get_htf_bias = Mock(return_value="bullish")

    allowed = watcher._check_zone_gating(event, mt5)

    assert allowed is True
