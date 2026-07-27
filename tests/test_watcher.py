from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from dataclasses import replace
from swift_scanner.config import ScannerConfig, SymbolScanConfig, TimeframeConfig, build_symbol_scan_configs
from swift_scanner.models import ScanResult, SignalEvent, SignalDirection, Zone
from swift_scanner.mt5_client import AutoTradingDisabledError
from swift_scanner.trade_db import TradeDB
from swift_scanner.watcher import PositionTracker, SwiftWatcher


def make_frame(rows):
    frame = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return frame


def make_event(symbol_actual: str, timeframe: str, direction: SignalDirection, label: str) -> SignalEvent:
    zone = Zone(
        zone_type="swift_signal",
        direction=direction,
        symbol_request=symbol_actual,
        symbol_actual=symbol_actual,
        created_at=datetime.now(timezone.utc),
        left_index=0,
        right_index=1,
        top=1.0,
        bottom=0.9,
        formed_index=1,
    )
    return SignalEvent(
        symbol_request=symbol_actual,
        symbol_actual=symbol_actual,
        timeframe=timeframe,
        triggered_at=datetime.now(timezone.utc),
        price=1.0,
        zone=zone,
        signal_label=label,
    )


def market_tick(ask: float, bid: float | None = None) -> SimpleNamespace:
    return SimpleNamespace(ask=ask, bid=ask if bid is None else bid)


def make_tracker(ticket: int) -> PositionTracker:
    return PositionTracker(
        ticket=ticket,
        symbol="EURUSD",
        direction="bullish",
        entry_price=1.10,
        volume=0.10,
        stop_loss=1.05,
        take_profit_1=1.12,
        take_profit_2=1.15,
        take_profit_3=1.18,
        tp1_qty=0.05,
        tp2_qty=0.03,
        tp3_qty=0.02,
        timeframe="M15",
    )


@pytest.fixture(autouse=True)
def allow_killzone(monkeypatch):
    monkeypatch.setattr("swift_scanner.watcher.is_in_killzone", lambda symbol: True)


def allow_profitable_gate(mock_mt5: Mock, event: SignalEvent) -> None:
    if event.take_profit_1 is None and event.take_profit is not None:
        event.take_profit_1 = event.take_profit

    if event.zone.direction == "bearish":
        h4_candles = [
            {"open": 1.10, "high": 1.12, "low": 1.09, "close": 1.11, "volume": 1}
            for _ in range(10)
        ] + [
            {"open": 1.08, "high": 1.09, "low": 1.06, "close": 1.07, "volume": 1}
            for _ in range(10)
        ]
        m15_candles = [
            {"open": 1.1000, "high": 1.1010, "low": 1.0950, "close": 1.0960, "volume": 1},
            {"open": 1.0990, "high": 1.1000, "low": 1.0940, "close": 1.0950, "volume": 1},
            {"open": 1.0980, "high": 1.0990, "low": 1.0930, "close": 1.0940, "volume": 1},
        ]
    else:
        h4_candles = [
            {"open": 1.07, "high": 1.08, "low": 1.05, "close": 1.06, "volume": 1}
            for _ in range(10)
        ] + [
            {"open": 1.10, "high": 1.13, "low": 1.09, "close": 1.12, "volume": 1}
            for _ in range(10)
        ]
        m15_candles = [
            {"open": 1.0950, "high": 1.1010, "low": 1.0940, "close": 1.1000, "volume": 1},
            {"open": 1.0960, "high": 1.1020, "low": 1.0950, "close": 1.1010, "volume": 1},
            {"open": 1.0970, "high": 1.1030, "low": 1.0960, "close": 1.1020, "volume": 1},
        ]

    h1_candles = h4_candles
    h2_candles = h4_candles

    def recent_candles(symbol: str, timeframe_name: str, count: int = 5):
        normalized_tf = timeframe_name.upper()
        if normalized_tf == "H4":
            return h4_candles[-count:]
        if normalized_tf == "H2":
            return h2_candles[-count:]
        if normalized_tf == "H1":
            return h1_candles[-count:]
        if normalized_tf == "M15":
            return m15_candles[-count:]
        return []

    mock_mt5.get_recent_candles.side_effect = recent_candles


def test_management_reasoning_keeps_trend_context_for_tp1(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    tracker = make_tracker(123)
    tracker.direction = "bullish"

    mt5 = Mock()
    mt5.get_recent_candles.return_value = [
        {"open": 1.1180, "high": 1.1210, "low": 1.1160, "close": 1.1205, "volume": 1},
        {"open": 1.1200, "high": 1.1230, "low": 1.1190, "close": 1.1225, "volume": 1},
        {"open": 1.1220, "high": 1.1250, "low": 1.1210, "close": 1.1245, "volume": 1},
    ]

    action, mood, reasons = watcher._build_management_decision(
        tracker,
        current_price=1.1215,
        mt5_client=mt5,
        action="PARTIAL_CLOSE",
        point=0.0001,
    )

    assert action == "PARTIAL_CLOSE"
    assert mood in {"calm", "confident"}
    assert any("trend still looks intact" in reason.lower() for reason in reasons)


def test_management_self_talk_uses_reasoning_for_partial_close(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    tracker = make_tracker(123)
    tracker.direction = "bullish"

    mt5 = Mock()
    mt5.get_recent_candles.return_value = [
        {"open": 1.1180, "high": 1.1210, "low": 1.1160, "close": 1.1205, "volume": 1},
        {"open": 1.1200, "high": 1.1230, "low": 1.1190, "close": 1.1225, "volume": 1},
        {"open": 1.1220, "high": 1.1250, "low": 1.1210, "close": 1.1245, "volume": 1},
    ]

    action, mood, reasons = watcher._build_management_decision(
        tracker,
        current_price=1.1215,
        mt5_client=mt5,
        action="PARTIAL_CLOSE",
        point=0.0001,
        reason="TP1 was reached, so the bot banked planned profit and reduced risk.",
    )

    self_talk = watcher._compose_decision_self_talk(
        action,
        mood,
        72.0,
        reasons,
    )

    assert action == "PARTIAL_CLOSE"
    assert "protect the position" in self_talk.lower()
    assert "I should protect the position" in self_talk
    assert any("runner" in reason.lower() for reason in reasons)


def test_trade_reasoning_uses_swift_signal_context(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.signal_tags = ["fair_value_gap", "discount_zone"]
    event.explanation = "price reclaimed the mean and the SWIFT crossover confirmed"

    mt5 = Mock()
    mt5.get_recent_candles.return_value = [
        {"open": 1.0950, "high": 1.1000, "low": 1.0940, "close": 1.0980, "volume": 1},
        {"open": 1.0980, "high": 1.1030, "low": 1.0970, "close": 1.1010, "volume": 1},
        {"open": 1.1010, "high": 1.1060, "low": 1.1000, "close": 1.1040, "volume": 1},
    ]

    reasons = watcher._build_trade_reasoning(
        event,
        mt5,
        market_tick(ask=1.1045, bid=1.1040),
        0.00001,
        82.0,
        60.0,
    )
    reason_text = " ".join(reasons).lower()

    assert "swift" in reason_text
    assert "main long entry" in reason_text
    assert "fair_value_gap" in reason_text
    assert "discount_zone" in reason_text


def test_trade_is_blocked_when_h2_bias_is_unknown_for_m15(tmp_path, capsys):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("BTCUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 100000.0
    event.price = 100000.0
    event.stop_loss = 99000.0
    event.take_profit_1 = 100681.818
    event.take_profit_2 = 101312.5
    event.take_profit_3 = 102100.0

    mock_mt5 = Mock()
    allow_profitable_gate(mock_mt5, event)
    watcher._get_htf_bias = Mock(return_value=None)

    result = watcher._is_trade_likely_profitable(event, mock_mt5, market_tick(ask=100001.0, bid=100000.0), 0.01)
    captured = capsys.readouterr()

    assert result is False
    assert "unable to determine H1 bias" in captured.out


def test_trade_is_blocked_by_conflicting_h2_bias_for_all_symbols(tmp_path, capsys):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("BTCUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 100000.0
    event.price = 100000.0
    event.stop_loss = 99000.0
    event.take_profit_1 = 100681.818
    event.take_profit_2 = 101312.5
    event.take_profit_3 = 102100.0

    mock_mt5 = Mock()
    allow_profitable_gate(mock_mt5, event)
    watcher._get_htf_bias = Mock(return_value="bearish")

    result = watcher._is_trade_likely_profitable(event, mock_mt5, market_tick(ask=100001.0, bid=100000.0), 0.01)
    captured = capsys.readouterr()

    assert result is False
    assert "[Gate] Blocked BTCUSD: H1 bias bearish conflicts with bullish trade" in captured.out


def test_history_annotation_recommends_reduce_for_weak_pattern(tmp_path, capsys):
    db_path = tmp_path / "trades.db"
    db = TradeDB(db_path=db_path)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.zone.zone_type = "premium_zone"

    for ticket, pnl in enumerate([0.02, -0.01, -0.02, -0.01, -0.01], start=1):
        signal_id = db.record_signal(event)
        db.record_execution(ticket, event, 0.10, signal_id=signal_id)
        db.record_close(ticket, 1.10 + pnl, "TP_FINAL", pnl, None)

    watcher = SwiftWatcher(ScannerConfig(output_dir=tmp_path))
    watcher.trade_db = db
    watcher._annotate_event_history(event)

    assert "weak pattern" in event.lesson
    assert "avoid or reducing" in event.lesson or "reducing size" in event.lesson
    assert "premium_zone" in event.lesson or "pattern" in event.lesson
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M5", "bearish", "Main Short Entry")
    event.entry_price = 1.1000
    event.price = 1.1000
    event.stop_loss = 1.1050
    event.take_profit_1 = 1.0950
    event.take_profit_2 = 1.0900
    event.take_profit_3 = 1.0850

    mock_mt5 = Mock()
    allow_profitable_gate(mock_mt5, event)
    watcher._get_htf_bias = Mock(return_value="bullish")

    result = watcher._is_trade_likely_profitable(event, mock_mt5, market_tick(ask=1.0990, bid=1.0985), 0.00001)
    captured = capsys.readouterr()

    assert result is False
    assert "[Gate] Blocked EURUSD: H1 bias bullish conflicts with bearish trade" in captured.out


def test_get_htf_bias_uses_recent_close_trend(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)

    bullish_candles = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1},
        {"open": 100.5, "high": 101.5, "low": 100.0, "close": 101.0, "volume": 1},
        {"open": 101.0, "high": 102.0, "low": 101.0, "close": 101.5, "volume": 1},
        {"open": 101.5, "high": 102.5, "low": 101.0, "close": 102.0, "volume": 1},
        {"open": 102.0, "high": 103.0, "low": 101.5, "close": 102.5, "volume": 1},
        {"open": 102.5, "high": 103.5, "low": 102.0, "close": 103.0, "volume": 1},
        {"open": 103.0, "high": 104.0, "low": 102.5, "close": 103.5, "volume": 1},
        {"open": 103.5, "high": 104.5, "low": 103.0, "close": 104.0, "volume": 1},
        {"open": 104.0, "high": 105.0, "low": 103.5, "close": 104.5, "volume": 1},
        {"open": 104.5, "high": 105.5, "low": 104.0, "close": 105.0, "volume": 1},
    ]
    mock_mt5 = Mock()
    mock_mt5.get_recent_candles.return_value = bullish_candles

    assert watcher._get_htf_bias("BTCUSD", mock_mt5, "H2") == "bullish"


def test_get_htf_bias_rejects_ranging_h2_data(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)

    ranging_candles = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.2, "volume": 1},
        {"open": 100.2, "high": 100.8, "low": 99.8, "close": 100.1, "volume": 1},
        {"open": 100.1, "high": 100.9, "low": 99.9, "close": 100.3, "volume": 1},
        {"open": 100.3, "high": 100.7, "low": 100.0, "close": 100.2, "volume": 1},
        {"open": 100.2, "high": 100.6, "low": 99.9, "close": 100.1, "volume": 1},
        {"open": 100.1, "high": 100.5, "low": 99.9, "close": 100.0, "volume": 1},
        {"open": 100.0, "high": 100.4, "low": 99.8, "close": 100.1, "volume": 1},
        {"open": 100.1, "high": 100.3, "low": 99.8, "close": 100.0, "volume": 1},
        {"open": 100.0, "high": 100.2, "low": 99.7, "close": 100.1, "volume": 1},
    ]
    mock_mt5 = Mock()
    mock_mt5.get_recent_candles.return_value = ranging_candles

    assert watcher._get_htf_bias("BTCUSD", mock_mt5, "H2") is None


def test_trade_is_allowed_with_matching_h1_bias_for_all_symbols(tmp_path, capsys):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1000
    event.price = 1.1000
    event.stop_loss = 1.0900
    event.take_profit_1 = 1.1100
    event.take_profit_2 = 1.1200
    event.take_profit_3 = 1.1300

    mock_mt5 = Mock()
    allow_profitable_gate(mock_mt5, event)
    watcher._get_htf_bias = Mock(return_value="bullish")

    result = watcher._is_trade_likely_profitable(event, mock_mt5, market_tick(ask=1.1001, bid=1.1000), 0.00001)
    captured = capsys.readouterr()

    assert result is True
    assert "[Gate]" in captured.out
    assert "placing trade" in captured.out


def test_symbol_quality_threshold_supersedes_global_threshold(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    config.min_signal_quality_score = 80
    symbol_cfg = config.symbol_scan_configs.get("XAUUSD")
    assert symbol_cfg is not None
    config.symbol_scan_configs["XAUUSD"] = replace(symbol_cfg, min_signal_quality_score=20)
    watcher = SwiftWatcher(config)

    event = make_event("XAUUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 2000.0
    event.price = 2000.0
    event.stop_loss = 1990.0
    event.take_profit = 2010.0
    event.take_profit_1 = 2010.0
    event.take_profit_2 = 2020.0
    event.take_profit_3 = 2030.0
    event.signal_source = "swift"

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.get_tick.return_value = market_tick(ask=2000.3, bid=2000.2)
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.01, "digits": 5}
    mock_mt5.normalize_order_volume.return_value = 0.02
    mock_mt5.place_market_order.return_value = 123
    watcher._is_trade_likely_profitable = Mock(return_value=True)
    watcher._score_signal_quality = Mock(return_value=30)

    status = watcher._execute_order_if_enabled(event, mock_mt5)

    assert status == "placed"
    mock_mt5.place_market_order.assert_called_once()


def test_reversal_after_tp2_does_not_close_before_returning_to_entry(tmp_path):
    cfg = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(cfg)
    ticket = 114
    tracker = make_tracker(ticket)
    tracker.tp1_closed = True
    tracker.tp2_closed = True
    tracker.tp3_closed = False
    tracker.tp2_reversal_armed = True
    watcher.open_positions[ticket] = tracker

    mock_mt5 = Mock()
    mock_mt5.get_open_positions.return_value = [SimpleNamespace(ticket=ticket)]
    mock_mt5.get_tick.return_value = market_tick(ask=1.1180, bid=1.1175)
    mock_mt5.close_position_partial.return_value = 999

    watcher._monitor_positions(mock_mt5)

    mock_mt5.close_position_partial.assert_not_called()
    assert ticket in watcher.open_positions


def test_handle_event_keeps_market_change_alert_disabled(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event_old = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    watcher.last_signal_direction[("EURUSD", "M15")] = "bearish"
    event_new = make_event("EURUSD", "M15", "bullish", "Main Long Entry")

    mock_result = Mock(fresh_events=[event_new])
    mock_mt5 = Mock()
    mock_mt5.place_market_order.return_value = 123
    watcher.config.enable_execution = False

    watcher.notifier = Mock()
    with patch("swift_scanner.watcher.save_signal_chart", return_value=Mock()) as _:
        watcher.handle_event(mock_result, event_new, mock_mt5)

    assert event_new.market_change_alert is False


def test_handle_event_tracks_direction_change_for_canonical_symbol(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    watcher.last_signal_direction[("BTCUSD", "M15")] = "bearish"

    event = make_event("BTCUSD", "M15", "bullish", "Main Long Entry")
    event.symbol_request = "BTCUSD"
    event.symbol_actual = "BTCUSDm"

    mock_result = Mock(fresh_events=[event])
    mock_mt5 = Mock()
    watcher.config.enable_execution = False
    watcher.notifier = Mock()

    with patch("swift_scanner.watcher.save_signal_chart", return_value=Mock()) as _:
        watcher.handle_event(mock_result, event, mock_mt5)

    assert event.market_change_alert is True
    assert watcher.last_signal_direction[("BTCUSD", "M15")] == "bullish"


def test_build_runtime_config_applies_symbol_scan_overrides(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    symbol_config = SymbolScanConfig(
        timeframes=[TimeframeConfig(name="M15", mt5_code=1)],
        use_trend_filter=False,
        scan_interval_seconds=60,
    )

    effective = watcher._build_runtime_config(symbol_config)
    assert effective.use_trend_filter is False

    override = watcher._build_runtime_config(symbol_config, trend_filter_override=True)
    assert override.use_trend_filter is True


def test_sync_event_take_profit_uses_configured_target(tmp_path):
    config = ScannerConfig(output_dir=tmp_path, tp_target_level="tp3")
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.take_profit_1 = 1.1100
    event.take_profit_2 = 1.1200
    event.take_profit_3 = 1.1300

    watcher._sync_event_take_profit(event)

    assert event.take_profit == 1.1300


def test_handle_event_does_not_notify_when_trade_is_blocked_by_gate(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("NASDAQ", "M15", "bullish", "Main Long Entry")
    event.entry_price = 100000.0
    event.price = 100000.0
    event.stop_loss = 99000.0
    event.take_profit = 101000.0
    event.take_profit_1 = 101000.0
    event.take_profit_2 = 102000.0
    event.take_profit_3 = 103000.0
    event.signal_source = "swift"

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.get_tick.return_value = market_tick(ask=100001.0, bid=100000.0)
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.01, "digits": 2}
    mock_mt5.normalize_order_volume.return_value = 0.02

    watcher.notifier = Mock()
    watcher._check_daily_loss_breaker = Mock(return_value=True)
    watcher._check_correlation_cap = Mock(return_value=True)
    watcher._check_news_blackout = Mock(return_value=True)
    watcher._is_trade_likely_profitable = Mock(return_value=False)
    watcher._score_signal_quality = Mock(return_value=90)
    watcher._calculate_equity_based_volume = Mock(return_value=None)

    with patch("swift_scanner.watcher.save_signal_chart", return_value=tmp_path / "chart.png"):
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5)

    watcher.notifier.notify.assert_not_called()
    assert event.event_id not in watcher.sent_alert_ids
    assert event.event_id not in watcher.pending_execution_events


def test_handle_event_bypasses_killzone_when_disabled(tmp_path, monkeypatch):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    config.disable_killzone_filter = True
    watcher = SwiftWatcher(config)
    event = make_event("XAUUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 2000.0
    event.price = 2000.0
    event.stop_loss = 1990.0
    event.take_profit = 2010.0
    event.take_profit_2 = 2020.0
    event.take_profit_3 = 2030.0
    event.signal_source = "swift"

    mock_result = Mock(fresh_events=[event])
    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.get_tick.return_value = market_tick(ask=2000.3, bid=2000.2)
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.01, "digits": 5}
    mock_mt5.normalize_order_volume.return_value = 0.02
    mock_mt5.place_market_order.return_value = 123
    mock_mt5.get_open_positions.return_value = [SimpleNamespace(ticket=123, price_open=2000.0)]

    watcher._check_daily_loss_breaker = Mock(return_value=True)
    watcher._check_correlation_cap = Mock(return_value=True)
    watcher._check_news_blackout = Mock(return_value=True)
    watcher._is_trade_likely_profitable = Mock(return_value=True)
    watcher._score_signal_quality = Mock(return_value=90)
    watcher._calculate_equity_based_volume = Mock(return_value=None)

    monkeypatch.setattr("swift_scanner.watcher.is_in_killzone", lambda symbol: False)
    watcher.notifier = Mock()

    with patch("swift_scanner.watcher.save_signal_chart", return_value=tmp_path / "chart.png"):
        watcher.handle_event(mock_result, event, mock_mt5)

    mock_mt5.place_market_order.assert_called_once()


def test_handle_event_retries_when_temporary_gate_blocks_entry(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1000
    event.price = 1.1000
    event.stop_loss = 1.0900
    event.take_profit = 1.1100
    event.take_profit_1 = 1.1100
    event.take_profit_2 = 1.1200
    event.take_profit_3 = 1.1300
    event.signal_source = "swift"

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.get_tick.return_value = market_tick(ask=1.1010, bid=1.1009)
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.00001, "digits": 5}
    mock_mt5.normalize_order_volume.return_value = 0.02
    mock_mt5.place_market_order.return_value = 123

    watcher.notifier = Mock()
    watcher._check_daily_loss_breaker = Mock(side_effect=[False, True])
    watcher._check_correlation_cap = Mock(return_value=True)
    watcher._check_news_blackout = Mock(return_value=True)
    watcher._is_trade_likely_profitable = Mock(return_value=True)
    watcher._score_signal_quality = Mock(return_value=90)
    watcher._calculate_equity_based_volume = Mock(return_value=None)

    with patch("swift_scanner.watcher.save_signal_chart", return_value=tmp_path / "chart.png"):
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5)

    assert event.event_id not in watcher.pending_execution_events
    assert watcher.notifier.notify.call_count == 1

    with patch("swift_scanner.watcher.save_signal_chart", return_value=tmp_path / "chart.png"):
        watcher.retry_pending_executions(mock_mt5)

    assert mock_mt5.place_market_order.call_count == 0
    assert event.event_id not in watcher.pending_execution_events


def test_execute_order_allows_small_move_beyond_max_entry_buffer(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1000
    event.price = 1.1000
    event.stop_loss = 1.0900
    event.take_profit = 1.1100
    event.take_profit_1 = 1.1100
    event.take_profit_2 = 1.1200
    event.take_profit_3 = 1.1300
    event.signal_source = "swift"

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.get_tick.return_value = market_tick(ask=1.10102, bid=1.10101)
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.00001, "digits": 5}
    mock_mt5.normalize_order_volume.return_value = 0.02
    mock_mt5.place_market_order.return_value = 123
    mock_mt5.get_open_positions.return_value = []

    watcher.notifier = Mock()
    watcher._check_daily_loss_breaker = Mock(return_value=True)
    watcher._check_correlation_cap = Mock(return_value=True)
    watcher._check_news_blackout = Mock(return_value=True)
    watcher._is_trade_likely_profitable = Mock(return_value=True)
    watcher._score_signal_quality = Mock(return_value=90)
    watcher._calculate_equity_based_volume = Mock(return_value=None)

    with patch("swift_scanner.watcher.save_signal_chart", return_value=tmp_path / "chart.png"):
        status = watcher._execute_order_if_enabled(
            event,
            mock_mt5,
            None,
            {"min": (0.0005, "price"), "max": (0.0010, "price")},
        )

    assert status == "placed"


def test_pending_signals_stop_after_max_confirmation_attempts(tmp_path):
    config = ScannerConfig(enable_execution=True, max_confirmation_attempts=2, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1000
    event.price = 1.1000
    event.stop_loss = 1.0900
    event.take_profit = 1.1100
    event.take_profit_1 = 1.1100
    event.take_profit_2 = 1.1200
    event.take_profit_3 = 1.1300
    event.signal_source = "swift"

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.get_tick.return_value = market_tick(ask=1.1000, bid=1.1000)
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.00001, "digits": 5}

    watcher.notifier = Mock()
    with patch("swift_scanner.watcher.save_signal_chart", return_value=tmp_path / "chart.png"):
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5)

    assert event.event_id in watcher.pending_execution_events

    with patch("swift_scanner.watcher.save_signal_chart", return_value=tmp_path / "chart.png"):
        watcher.retry_pending_executions(mock_mt5)

    assert event.event_id not in watcher.pending_execution_events
    assert watcher.pending_confirmation_attempts == {}


@pytest.mark.parametrize(
    ("symbol", "expected_min", "expected_max", "unit"),
    [
        ("XAUUSD", 11.25, 18.75, "pips"),
        ("USOIL", 0.0375, 0.06, "price"),
        ("GBPUSD", 2.25, 3.75, "pips"),
        ("EURUSD", 1.5, 3.0, "pips"),
        ("USDJPY", 2.25, 3.75, "pips"),
        ("AUDUSD", 1.5, 3.0, "pips"),
        ("US30", 11.25, 18.75, "points"),
        ("NASDAQ", 3.75, 7.5, "points"),
        ("BTCUSD", 0.075, 0.1125, "percent"),
        ("ETHUSD", 0.1125, 0.15, "percent"),
    ],
)
def test_build_runtime_config_uses_tightened_entry_buffers(
    tmp_path,
    symbol,
    expected_min,
    expected_max,
    unit,
):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    symbol_config = build_symbol_scan_configs()[symbol]

    _, _, entry_buffer = watcher._build_runtime_config(symbol_config)

    assert entry_buffer == {
        "min": (expected_min, unit),
        "max": (expected_max, unit),
    }


@pytest.mark.parametrize(
    (
        "symbol",
        "actual",
        "entry",
        "point",
        "digits",
        "expected_sl",
        "expected_tp1",
        "expected_tp2",
        "expected_tp3",
    ),
    [
        ("XAUUSD", "XAUUSDm", 2000.0, 0.01, 2, 0.55, 0.4284, 0.825, 1.32),
        ("BTCUSD", "BTCUSDm", 100000.0, 0.01, 2, 1.1, 0.778909, 1.5, 2.4),
        ("USOIL", "WTIUSDm", 70.0, 0.001, 3, 0.30, 0.21875, 0.4375, 0.75),
        ("NASDAQ", "USTEC_x100m", 20000.0, 0.1, 1, 88.0, 60.0, 120.0, 240.0),
        ("US30", "US30_x10m", 40000.0, 0.1, 1, 132.0, 99.0, 198.0, 316.8),
        ("GBPUSD", "GBPUSDm", 1.2500, 0.00001, 5, 0.0033, 0.00307125, 0.005265, 0.00702),
        ("USDJPY", "USDJPYm", 150.00, 0.001, 3, 0.33, 0.28, 0.48, 0.72),
        ("EURUSD", "EURUSDm", 1.1000, 0.00001, 5, 0.0022, 0.0017142872, 0.0030857128, 0.0048),
        ("AUDUSD", "AUDUSDm", 0.6600, 0.00001, 5, 0.001375, 0.0009, 0.0015, 0.00225),
        ("ETHUSD", "ETHUSDm", 4000.0, 0.01, 2, 1.65, 1.2, 2.4, 3.6),
    ],
)
def test_process_symbol_converts_configured_risk_before_scan(
    tmp_path,
    symbol,
    actual,
    entry,
    point,
    digits,
    expected_sl,
    expected_tp1,
    expected_tp2,
    expected_tp3,
):
    config = ScannerConfig(enable_execution=False, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    runtime_config, raw_overrides, entry_buffer = watcher._build_runtime_config(
        build_symbol_scan_configs()[symbol]
    )
    bars = make_frame(
        [
            ("2026-05-25 00:00:00+00:00", entry, entry, entry, entry, 1000),
        ]
    )
    mock_mt5 = Mock()
    mock_mt5.fetch_rates.return_value = bars
    mock_mt5.get_symbol_market_status.return_value = {"point": point, "digits": digits}

    with patch("swift_scanner.watcher.scan_swift", return_value=Mock(fresh_events=[])) as scan:
        watcher.process_symbol(
            mock_mt5,
            symbol,
            actual,
            TimeframeConfig(name="M15", mt5_code=1),
            runtime_config,
            raw_overrides,
            entry_buffer,
        )

    scan_config = scan.call_args.args[4]
    if symbol in {"BTCUSD", "ETHUSD", "XAUUSD"}:
        assert scan_config.main_sl_percent == pytest.approx(expected_sl, rel=1e-9)
        assert scan_config.main_tp1_percent == pytest.approx(expected_tp1, rel=1e-9)
        assert scan_config.main_tp2_percent == pytest.approx(expected_tp2, rel=1e-9)
        assert scan_config.main_tp3_percent == pytest.approx(expected_tp3, rel=1e-9)
    else:
        assert scan_config.main_sl_delta == pytest.approx(expected_sl, rel=1e-9)
        assert scan_config.main_tp1_delta == pytest.approx(expected_tp1, rel=1e-9)
        assert scan_config.main_tp2_delta == pytest.approx(expected_tp2, rel=1e-9)
        assert scan_config.main_tp3_delta == pytest.approx(expected_tp3, rel=1e-9)


def test_handle_event_enforces_symbol_risk_without_explicit_overrides(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("NASDAQ", "M15", "bearish", "Main Short Entry")
    event.symbol_actual = "USTEC_x100m"
    event.entry_price = 29338.46
    event.price = 29338.46
    event.stop_loss = 29378.46
    event.take_profit = 29058.46
    event.take_profit_1 = 29258.46
    event.take_profit_2 = 29188.46
    event.take_profit_3 = 29058.46

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.normalize_order_volume.return_value = 0.01
    mock_mt5.get_tick.return_value = market_tick(ask=29334.0, bid=29333.7)
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.1, "digits": 1}
    mock_mt5.place_market_order.return_value = 12345
    mock_mt5.get_open_positions.return_value = [
        SimpleNamespace(ticket=12345, price_open=29333.7)
    ]
    allow_profitable_gate(mock_mt5, event)

    watcher.notifier = Mock()
    with patch("swift_scanner.watcher.save_signal_chart", return_value=Path("mock.png")):
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5)

    assert event.stop_loss == pytest.approx(29408.46, rel=1e-9)
    assert event.take_profit_1 == pytest.approx(29299.96, rel=1e-9)
    assert event.take_profit_2 == pytest.approx(29261.46, rel=1e-9)
    assert event.take_profit_3 == pytest.approx(29184.46, rel=1e-9)
    _, _, kwargs = mock_mt5.place_market_order.mock_calls[0]
    assert kwargs["stop_loss"] == pytest.approx(29408.46, rel=1e-9)
    assert kwargs["take_profit"] == pytest.approx(29184.46, rel=1e-9)


def test_execute_order_skips_when_open_position_exists(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.stop_loss = 1.0
    event.take_profit = 1.1

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = True
    mock_mt5.is_symbol_tradeable.return_value = True

    watcher.notifier = Mock()

    with patch("swift_scanner.watcher.save_signal_chart", return_value=Mock()) as _:
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5)

    assert event.order_ticket is None
    mock_mt5.place_market_order.assert_not_called()


def test_execute_order_skips_when_execution_disabled(tmp_path):
    config = ScannerConfig(enable_execution=False, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.stop_loss = 1.0
    event.take_profit = 1.1

    mock_mt5 = Mock()
    watcher.notifier = Mock()

    with patch("swift_scanner.watcher.save_signal_chart", return_value=Mock()) as _:
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5)

    assert event.order_ticket is None
    mock_mt5.place_market_order.assert_not_called()
    watcher.notifier.notify.assert_called_once()


def test_execute_order_raises_when_symbol_not_tradeable(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.stop_loss = 1.0
    event.take_profit = 1.1

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = False

    watcher.notifier = Mock()

    with patch("swift_scanner.watcher.save_signal_chart", return_value=Mock()) as _:
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5)

    mock_mt5.place_market_order.assert_not_called()


def test_execute_order_skips_non_entry_signals(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long TP1")
    event.stop_loss = 1.0
    event.take_profit = 1.1

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True

    watcher.notifier = Mock()

    with patch("swift_scanner.watcher.save_signal_chart", return_value=Mock()) as _:
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5)

    mock_mt5.place_market_order.assert_not_called()


def test_execute_order_places_real_mt5_order_when_buffer_is_met(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1
    event.price = 1.1
    event.stop_loss = 1.095
    event.take_profit = 1.12

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.normalize_order_volume.return_value = 0.1
    mock_mt5.get_tick.return_value = market_tick(ask=1.1006, bid=1.1005)
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.0001}
    mock_mt5.place_market_order.return_value = 12345
    allow_profitable_gate(mock_mt5, event)

    watcher.notifier = Mock()

    with patch("swift_scanner.watcher.save_signal_chart", return_value=Path("mock.png")):
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5)

    assert event.order_ticket == 12345
    mock_mt5.place_market_order.assert_called_once()
    assert event.event_id in watcher.sent_alert_ids
    assert event.event_id not in watcher.pending_execution_events
    watcher.notifier.notify.assert_called_once()


def test_execute_order_normalizes_tiny_nasdaq_partial_closes(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("NASDAQ", "M15", "bearish", "Main Short Entry")
    event.symbol_actual = "USTEC_x100m"
    event.entry_price = 29338.46
    event.price = 29338.46
    event.stop_loss = 29378.46
    event.take_profit = 29058.46
    event.take_profit_1 = 29258.46
    event.take_profit_2 = 29188.46
    event.take_profit_3 = 29058.46

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.normalize_order_volume.side_effect = lambda symbol, volume: 0.01 if volume > 0 else 0.0
    mock_mt5.get_tick.return_value = market_tick(ask=29330.0, bid=29328.7)
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.01, "digits": 2}
    mock_mt5.place_market_order.return_value = 2900597494
    mock_mt5.get_open_positions.return_value = [
        SimpleNamespace(ticket=2900597494, price_open=29328.7)
    ]
    allow_profitable_gate(mock_mt5, event)

    watcher.notifier = Mock()
    with patch("swift_scanner.watcher.save_signal_chart", return_value=Path("mock.png")):
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5)

    tracker = watcher.open_positions[2900597494]
    assert tracker.entry_price == pytest.approx(29328.7)
    assert tracker.tp1_qty == pytest.approx(0.01)
    assert tracker.tp2_qty == 0.0
    assert tracker.tp3_qty == 0.0


def test_execute_order_retries_pending_buffer_before_marking_sent(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1
    event.price = 1.1
    event.stop_loss = 1.095
    event.take_profit = 1.12

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.normalize_order_volume.return_value = 0.1
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.0001}
    mock_mt5.get_tick.return_value = market_tick(ask=1.1001, bid=1.1000)
    mock_mt5.place_market_order.return_value = 12345
    allow_profitable_gate(mock_mt5, event)

    watcher.notifier = Mock()

    with patch("swift_scanner.watcher.save_signal_chart", return_value=Path("mock.png")):
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5)

    mock_mt5.place_market_order.assert_not_called()
    watcher.notifier.notify.assert_not_called()
    assert event.event_id in watcher.pending_execution_events
    assert event.event_id not in watcher.sent_alert_ids

    mock_mt5.get_tick.return_value = market_tick(ask=1.1006, bid=1.1005)
    watcher.retry_pending_executions(mock_mt5)

    assert event.order_ticket == 12345
    mock_mt5.place_market_order.assert_called_once()
    assert event.event_id not in watcher.pending_execution_events
    assert event.event_id in watcher.sent_alert_ids
    watcher.notifier.notify.assert_called_once()


def test_allows_opposite_side_entry_while_same_direction_is_pending(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    first = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    first.entry_price = 1.1000
    first.price = 1.1000
    first.stop_loss = 1.0950
    first.take_profit = 1.1200

    second = make_event("EURUSD", "M30", "bearish", "Main Short Entry")
    second.entry_price = 1.1000
    second.price = 1.1000
    second.stop_loss = 1.1050
    second.take_profit = 1.0800

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.normalize_order_volume.return_value = 0.1
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.0001}
    mock_mt5.get_tick.return_value = market_tick(ask=1.1001, bid=1.0997)
    mock_mt5.place_market_order.return_value = 12345

    watcher.notifier = Mock()
    watcher._check_daily_loss_breaker = Mock(return_value=True)
    watcher._check_correlation_cap = Mock(return_value=True)
    watcher._check_news_blackout = Mock(return_value=True)
    watcher._is_trade_likely_profitable = Mock(return_value=True)
    watcher._score_signal_quality = Mock(return_value=90)
    watcher._calculate_equity_based_volume = Mock(return_value=None)

    with patch("swift_scanner.watcher.save_signal_chart", return_value=Path("mock.png")):
        watcher.handle_event(Mock(fresh_events=[first]), first, mock_mt5)
        watcher.handle_event(Mock(fresh_events=[second]), second, mock_mt5)

    mock_mt5.place_market_order.assert_called_once()
    assert first.event_id in watcher.pending_execution_events
    assert second.event_id not in watcher.pending_execution_events
    assert second.event_id in watcher.sent_alert_ids
    assert second.order_ticket == 12345


def test_skips_same_direction_pending_entry_for_same_pair(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    first = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    first.entry_price = 1.1000
    first.price = 1.1000
    first.stop_loss = 1.0950
    first.take_profit = 1.1200

    second = make_event("EURUSD", "M30", "bullish", "Main Long Entry")
    second.entry_price = 1.1000
    second.price = 1.1000
    second.stop_loss = 1.0955
    second.take_profit = 1.1205

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.normalize_order_volume.return_value = 0.1
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.0001}
    mock_mt5.get_tick.return_value = market_tick(ask=1.1001, bid=1.0997)
    mock_mt5.place_market_order.return_value = 12345

    watcher.notifier = Mock()
    watcher._check_daily_loss_breaker = Mock(return_value=True)
    watcher._check_correlation_cap = Mock(return_value=True)
    watcher._check_news_blackout = Mock(return_value=True)
    watcher._is_trade_likely_profitable = Mock(return_value=True)
    watcher._score_signal_quality = Mock(return_value=90)
    watcher._calculate_equity_based_volume = Mock(return_value=None)

    with patch("swift_scanner.watcher.save_signal_chart", return_value=Path("mock.png")):
        watcher.handle_event(Mock(fresh_events=[first]), first, mock_mt5)
        watcher.handle_event(Mock(fresh_events=[second]), second, mock_mt5)

    assert mock_mt5.place_market_order.call_count == 0
    assert first.event_id in watcher.pending_execution_events
    assert second.event_id not in watcher.pending_execution_events
    assert second.event_id in watcher.sent_alert_ids
    assert second.order_ticket is None


def test_tracks_opposite_direction_pending_entries_separately(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    first = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    first.entry_price = 1.1000
    first.price = 1.1000
    first.stop_loss = 1.0950
    first.take_profit = 1.1200

    second = make_event("EURUSD", "M30", "bearish", "Main Short Entry")
    second.entry_price = 1.1000
    second.price = 1.1000
    second.stop_loss = 1.1050
    second.take_profit = 1.0800

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.normalize_order_volume.return_value = 0.1
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.0001}
    mock_mt5.get_tick.return_value = market_tick(ask=1.1001, bid=1.0999)
    mock_mt5.place_market_order.return_value = 12345

    watcher.notifier = Mock()
    watcher._check_daily_loss_breaker = Mock(return_value=True)
    watcher._check_correlation_cap = Mock(return_value=True)
    watcher._check_news_blackout = Mock(return_value=True)
    watcher._is_trade_likely_profitable = Mock(return_value=True)
    watcher._score_signal_quality = Mock(return_value=90)
    watcher._calculate_equity_based_volume = Mock(return_value=None)

    with patch("swift_scanner.watcher.save_signal_chart", return_value=Path("mock.png")):
        watcher.handle_event(Mock(fresh_events=[first]), first, mock_mt5)
        watcher.handle_event(Mock(fresh_events=[second]), second, mock_mt5)

    assert mock_mt5.place_market_order.call_count == 0
    assert first.event_id in watcher.pending_execution_events
    assert second.event_id in watcher.pending_execution_events


def test_retry_pending_execution_finishes_permanent_autotrading_failure(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1
    event.price = 1.1
    event.stop_loss = 1.095
    event.take_profit = 1.12

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.normalize_order_volume.return_value = 0.1
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.0001}
    mock_mt5.get_tick.return_value = market_tick(ask=1.1001, bid=1.1000)
    mock_mt5.place_market_order.side_effect = AutoTradingDisabledError(
        "MT5 order failed: retcode=10027, comment=AutoTrading disabled by client, mt5.last_error()=(1, 'Success')"
    )
    allow_profitable_gate(mock_mt5, event)

    watcher.notifier = Mock()

    with patch("swift_scanner.watcher.save_signal_chart", return_value=Path("mock.png")):
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5)

    assert event.event_id in watcher.pending_execution_events
    assert event.event_id not in watcher.sent_alert_ids

    mock_mt5.get_tick.return_value = market_tick(ask=1.1006, bid=1.1005)
    watcher.retry_pending_executions(mock_mt5)

    assert event.event_id not in watcher.pending_execution_events
    assert event.event_id in watcher.sent_alert_ids
    watcher.notifier.notify.assert_called_once()


def test_retry_pending_execution_increments_confirmation_attempts(tmp_path):
    config = ScannerConfig(enable_execution=True, max_confirmation_attempts=3, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1000
    event.price = 1.1000
    event.stop_loss = 1.0950
    event.take_profit = 1.1200
    event.take_profit_1 = 1.1200
    event.take_profit_2 = 1.1300
    event.take_profit_3 = 1.1400
    event.signal_source = "swift"

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.normalize_order_volume.return_value = 0.1
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.00001, "digits": 5}
    mock_mt5.get_tick.return_value = market_tick(ask=1.1000, bid=1.1000)
    mock_mt5.place_market_order.return_value = 12345
    allow_profitable_gate(mock_mt5, event)

    watcher.notifier = Mock()
    with patch("swift_scanner.watcher.save_signal_chart", return_value=tmp_path / "chart.png"):
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5)

    assert event.event_id in watcher.pending_execution_events
    initial_attempts = watcher.pending_confirmation_attempts.get(event.event_id, 0)

    with patch("swift_scanner.watcher.save_signal_chart", return_value=tmp_path / "chart.png"):
        watcher.retry_pending_executions(mock_mt5)

    assert event.event_id in watcher.pending_execution_events
    assert watcher.pending_confirmation_attempts[event.event_id] == initial_attempts + 1
    mock_mt5.place_market_order.assert_not_called()


def test_retry_pending_execution_succeeds_after_pending(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1000
    event.price = 1.1000
    event.stop_loss = 1.0950
    event.take_profit = 1.1200
    event.take_profit_1 = 1.1200
    event.take_profit_2 = 1.1300
    event.take_profit_3 = 1.1400
    event.signal_source = "swift"

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.normalize_order_volume.return_value = 0.1
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.00001, "digits": 5}
    mock_mt5.get_tick.return_value = market_tick(ask=1.1000, bid=1.1000)
    mock_mt5.place_market_order.return_value = 12345
    allow_profitable_gate(mock_mt5, event)

    watcher.notifier = Mock()
    with patch("swift_scanner.watcher.save_signal_chart", return_value=Path("mock.png")):
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5)

    assert event.event_id in watcher.pending_execution_events
    assert event.event_id not in watcher.sent_alert_ids

    mock_mt5.get_tick.return_value = market_tick(ask=1.1006, bid=1.1005)
    with patch("swift_scanner.watcher.save_signal_chart", return_value=Path("mock.png")):
        watcher.retry_pending_executions(mock_mt5)

    assert event.order_ticket == 12345
    assert event.event_id not in watcher.pending_execution_events
    assert event.event_id in watcher.sent_alert_ids
    watcher.notifier.notify.assert_called_once()


def test_execute_order_applies_raw_overrides_to_tp_and_sl(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1000
    event.price = 1.1000
    event.take_profit = 1.1200
    event.stop_loss = 1.0900

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.normalize_order_volume.return_value = 0.1
    mock_mt5.get_tick.return_value = market_tick(ask=1.1006, bid=1.1005)
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.0001}
    mock_mt5.place_market_order.return_value = 12345
    allow_profitable_gate(mock_mt5, event)

    raw_overrides = {
        "main_tp1": (50.0, "pips"),
        "main_sl": (0.0100, "price"),
    }

    watcher.notifier = Mock()
    with patch("swift_scanner.watcher.save_signal_chart", return_value=Path("mock.png")):
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5, raw_overrides)

    assert event.order_ticket == 12345
    assert event.take_profit_1 == pytest.approx(1.1050, rel=1e-6)
    assert event.stop_loss == pytest.approx(1.0900, rel=1e-6)


def test_execute_order_uses_true_pip_size_for_five_digit_forex(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1000
    event.price = 1.1000
    event.take_profit = 1.1200
    event.stop_loss = 1.0900

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.normalize_order_volume.return_value = 0.1
    mock_mt5.get_tick.return_value = market_tick(ask=1.1006, bid=1.1005)
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.00001, "digits": 5}
    mock_mt5.place_market_order.return_value = 12345
    allow_profitable_gate(mock_mt5, event)

    raw_overrides = {
        "main_tp1": (50.0, "pips"),
        "main_sl": (20.0, "pips"),
    }

    watcher.notifier = Mock()
    with patch("swift_scanner.watcher.save_signal_chart", return_value=Path("mock.png")):
        watcher.handle_event(Mock(fresh_events=[event]), event, mock_mt5, raw_overrides)

    assert event.order_ticket == 12345
    assert event.take_profit_1 == pytest.approx(1.1050, rel=1e-6)
    assert event.stop_loss == pytest.approx(1.0980, rel=1e-6)


def test_execute_order_skips_when_price_exceeds_max_entry_buffer(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1000
    event.price = 1.1000
    event.stop_loss = 1.0950
    event.take_profit = 1.1200
    event.take_profit_1 = 1.1050

    mock_mt5 = Mock()
    mock_mt5.has_open_position.return_value = False
    mock_mt5.is_symbol_tradeable.return_value = True
    mock_mt5.get_tick.return_value = market_tick(ask=1.1010, bid=1.1009)
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.0001}

    status = watcher._execute_order_if_enabled(
        event,
        mock_mt5,
        entry_buffer_override={
            "min": (2.0, "pips"),
            "max": (5.0, "pips"),
        },
    )

    assert status == "pending"
    mock_mt5.place_market_order.assert_not_called()


def test_gate_blocks_when_required_market_data_fails(tmp_path):
    config = ScannerConfig(enable_execution=True, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1000
    event.price = 1.1000
    event.stop_loss = 1.0950
    event.take_profit = 1.1200
    event.take_profit_1 = 1.1050

    mock_mt5 = Mock()
    mock_mt5.get_recent_candles.side_effect = RuntimeError("MT5 data unavailable")

    assert watcher._is_trade_likely_profitable(
        event,
        mock_mt5,
        market_tick(ask=1.1006, bid=1.1005),
        0.0001,
    ) is False


def test_position_trackers_are_persisted_and_loaded(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    tracker = watcher.open_positions[123] = PositionTracker(
        ticket=123,
        symbol="EURUSD",
        direction="bullish",
        entry_price=1.1000,
        volume=0.1,
        stop_loss=1.0900,
        take_profit_1=1.1050,
        take_profit_2=1.1100,
        take_profit_3=1.1200,
        tp1_qty=0.05,
        tp2_qty=0.03,
        tp3_qty=0.02,
        tp1_closed=True,
    )

    watcher.save_position_trackers()

    payload = json.loads((tmp_path / "open_positions.json").read_text(encoding="utf-8"))
    assert payload["open_positions"][0]["ticket"] == 123

    reloaded = SwiftWatcher(ScannerConfig(output_dir=tmp_path))

    assert reloaded.open_positions[123].symbol == tracker.symbol
    assert reloaded.open_positions[123].tp1_closed is True


@pytest.mark.parametrize(
    (
        "symbol",
        "timeframe",
        "entry",
        "point",
        "digits",
        "expected_sl",
        "expected_tp1",
        "expected_tp2",
        "expected_tp3",
    ),
    [
        ("XAUUSD", "M5", 2000.0, 0.01, 2, 1992.3, 2005.9976, 2011.55, 2018.48),
        ("XAUUSD", "M15", 2000.0, 0.01, 2, 1989.0, 2008.568, 2016.5, 2026.4),
        ("BTCUSD", "M15", 100000.0, 0.01, 2, 99120.0, 100624.0, 101200.0, 101920.0),
        ("USOIL", "M15", 70.0, 0.001, 3, 69.7, 70.21875, 70.4375, 70.75),
        ("NASDAQ", "M5", 20000.0, 0.1, 1, 19945.0, 20048.0, 20096.0, 20192.0),
        ("NASDAQ", "M15", 20000.0, 0.1, 1, 19967.0, 20042.0, 20084.0, 20168.0),
        ("US30", "M15", 40000.0, 0.1, 1, 39868.0, 40099.0, 40198.0, 40316.8),
        ("GBPUSD", "M15", 1.2500, 0.00001, 5, 1.2467, 1.25307125, 1.255265, 1.25702),
        ("USDJPY", "M15", 150.00, 0.001, 3, 149.67, 150.28, 150.48, 150.72),
        ("EURUSD", "M15", 1.1000, 0.00001, 5, 1.0978, 1.1017142872, 1.1030857128, 1.1048),
        ("AUDUSD", "M15", 0.6600, 0.00001, 5, 0.658625, 0.6609, 0.6615, 0.66225),
        ("ETHUSD", "M15", 4000.0, 0.01, 2, 3947.2, 4038.4, 4076.8, 4115.2),
    ],
)
def test_configured_main_alert_levels_match_risk_table(
    tmp_path,
    symbol,
    timeframe,
    entry,
    point,
    digits,
    expected_sl,
    expected_tp1,
    expected_tp2,
    expected_tp3,
):
    config = ScannerConfig(enable_execution=False, output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event(symbol, timeframe, "bullish", "Main Long Entry")
    event.entry_price = entry
    event.price = entry
    event.stop_loss = entry * 0.995
    event.take_profit_1 = entry * 1.01
    event.take_profit_2 = entry * 1.015
    event.take_profit_3 = entry * 1.02
    event.take_profit = event.take_profit_3

    symbol_config = build_symbol_scan_configs()[symbol]
    _, raw_overrides, _ = watcher._build_runtime_config(symbol_config, timeframe_name=event.timeframe)

    mock_mt5 = Mock()
    mock_mt5.get_symbol_market_status.return_value = {"point": point, "digits": digits}

    watcher._apply_raw_overrides_to_event(event, mock_mt5, raw_overrides)

    assert event.stop_loss == pytest.approx(expected_sl, rel=1e-9)
    assert event.take_profit_1 == pytest.approx(expected_tp1, rel=1e-9)
    assert event.take_profit_2 == pytest.approx(expected_tp2, rel=1e-9)
    assert event.take_profit_3 == pytest.approx(expected_tp3, rel=1e-9)
    assert event.take_profit == pytest.approx(expected_tp3, rel=1e-9)


def test_monitor_positions_closes_remaining_when_price_reverses_after_tp1(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    watcher.config.order_deviation = 10
    watcher.config.order_magic_number = 123

    tracker = __import__('types').SimpleNamespace(
        ticket=123,
        symbol='EURUSD',
        direction='bullish',
        entry_price=1.1000,
        volume=0.1,
        stop_loss=1.0900,
        take_profit_1=1.1050,
        take_profit_2=1.1100,
        take_profit_3=1.1200,
        tp1_qty=0.03,
        tp2_qty=0.03,
        tp3_qty=0.04,
        tp1_closed=True,
        tp2_closed=False,
        tp3_closed=False,
        tp1_reversal_armed=True,
        opposite_pending_closed=set(),
        get_remaining_volume=lambda: 0.07,
    )
    watcher.open_positions[123] = tracker

    mock_mt5 = Mock()
    mock_mt5.get_open_positions.return_value = [SimpleNamespace(ticket=123)]
    mock_mt5.get_tick.return_value = market_tick(ask=1.0995, bid=1.0994)
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.00001}
    mock_mt5.close_position_partial.return_value = 987

    # Add an opposite-direction pending entry for the same symbol
    opposite_event = make_event('EURUSD', 'M15', 'bearish', 'Main Short Entry')
    watcher.pending_execution_events[opposite_event.event_id] = (
        opposite_event,
        None,
        Mock(spec=ScanResult),
    )

    watcher._monitor_positions(mock_mt5)

    mock_mt5.close_position_partial.assert_called_once_with(
        'EURUSD',
        'bullish',
        0.07,
        position_ticket=123,
        deviation=watcher.config.order_deviation,
        magic=watcher.config.order_magic_number,
    )
    assert opposite_event.event_id in tracker.opposite_pending_closed
    assert watcher.pending_execution_events[opposite_event.event_id][0] is opposite_event


def test_cleanup_stale_opposite_pending_ids(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)

    active_event = make_event('EURUSD', 'M15', 'bearish', 'Main Short Entry')
    watcher.pending_execution_events[active_event.event_id] = (
        active_event,
        None,
        Mock(spec=ScanResult),
    )

    tracker = __import__('types').SimpleNamespace(
        ticket=123,
        symbol='EURUSD',
        direction='bullish',
        entry_price=1.1000,
        volume=0.1,
        stop_loss=1.0900,
        take_profit_1=1.1050,
        take_profit_2=1.1100,
        take_profit_3=1.1200,
        tp1_qty=0.03,
        tp2_qty=0.03,
        tp3_qty=0.04,
        tp1_closed=True,
        tp2_closed=False,
        tp3_closed=False,
        tp1_reversal_armed=True,
        opposite_pending_closed={"deadbeef", active_event.event_id},
        get_remaining_volume=lambda: 0.07,
    )
    watcher.open_positions[123] = tracker

    mock_mt5 = Mock()
    mock_mt5.get_open_positions.return_value = [SimpleNamespace(ticket=123)]
    mock_mt5.get_tick.return_value = market_tick(ask=1.0995, bid=1.0994)
    mock_mt5.get_symbol_market_status.return_value = {"point": 0.00001}

    watcher._cleanup_stale_opposite_pending_ids()

    assert getattr(tracker, "opposite_pending_closed", set()) == {active_event.event_id}


def test_parent_bias_timeframe_mapping():
    config = ScannerConfig(output_dir=Path("."))
    watcher = SwiftWatcher(config)

    assert watcher._parent_bias_timeframe("M5") == "H1"
    assert watcher._parent_bias_timeframe("M15") == "H1"
    assert watcher._parent_bias_timeframe("m15") == "H1"
    assert watcher._parent_bias_timeframe("H1") == "H2"
    assert watcher._parent_bias_timeframe("H2") == "H4"
    assert watcher._parent_bias_timeframe("H4") is None
    assert watcher._parent_bias_timeframe("D1") is None


def test_score_signal_quality_calculates_confluent_score(tmp_path):
    """Test that signal quality scoring combines momentum, HTF alignment, freshness, commitment."""
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    
    # Bullish entry with strong confluence
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1000
    event.stop_loss = 1.0950
    event.take_profit_1 = 1.1050
    
    mock_mt5 = Mock()
    # Strong bullish momentum (2 consecutive bull candles)
    mock_mt5.get_recent_candles.side_effect = lambda symbol, timeframe, count: (
        [
            {"open": 1.0950, "high": 1.1010, "low": 1.0940, "close": 1.1000, "volume": 1},
            {"open": 1.0960, "high": 1.1020, "low": 1.0950, "close": 1.1010, "volume": 1},
            {"open": 1.0970, "high": 1.1030, "low": 1.0960, "close": 1.1020, "volume": 1},
        ] if timeframe.upper() == "M15" else []
    )
    
    tick = market_tick(ask=1.1005, bid=1.1004)
    score = watcher._score_signal_quality(event, mock_mt5, tick, 0.0001)
    
    # Should be high: base 35 + momentum 20 + H4 bias 0-15 (mocked) + freshness 10 + commitment varies
    # Expected: >= 50 with strong confluence
    assert score >= 50
    assert score <= 100


def test_score_signal_quality_penalizes_late_entry(tmp_path):
    """Test that signal quality score drops when TP1 is already partially exhausted."""
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    
    # Entry price already 70% of the way to TP1 (late entry)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1000
    event.stop_loss = 1.0950
    event.take_profit_1 = 1.1050
    
    mock_mt5 = Mock()
    mock_mt5.get_recent_candles.return_value = []
    
    # Price already moved 70% of the way to TP1: score should be lower
    tick = market_tick(ask=1.1035, bid=1.1034)  # 70% of move already happened
    score = watcher._score_signal_quality(event, mock_mt5, tick, 0.0001)
    
    # Should be lower due to late entry (missing +10 pts for freshness)
    assert score < 60


def test_delay_partial_close_when_momentum_is_strong(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    config.partial_close_delay_on_strong_momentum = True
    config.partial_close_momentum_candles = 4
    config.partial_close_momentum_min_aligned_ratio = 0.75
    watcher = SwiftWatcher(config)

    tracker = make_tracker(123)
    tracker.ticket = 123
    tracker.symbol = "EURUSD"
    tracker.timeframe = "M15"
    tracker.direction = "bullish"
    tracker.take_profit_1 = 1.1050
    watcher.open_positions[tracker.ticket] = tracker

    mock_mt5 = Mock()
    mock_mt5.get_recent_candles.return_value = [
        {"open": 1.1000, "high": 1.1030, "low": 1.0990, "close": 1.1010, "volume": 1},
        {"open": 1.1010, "high": 1.1040, "low": 1.1000, "close": 1.1020, "volume": 1},
        {"open": 1.1020, "high": 1.1050, "low": 1.1010, "close": 1.1030, "volume": 1},
        {"open": 1.1030, "high": 1.1060, "low": 1.1020, "close": 1.1050, "volume": 1},
    ]

    assert watcher._should_delay_partial_close(tracker, mock_mt5, 1) is True


def test_do_not_delay_partial_close_when_momentum_weak(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    config.partial_close_delay_on_strong_momentum = True
    config.partial_close_momentum_candles = 4
    config.partial_close_momentum_min_aligned_ratio = 0.75
    watcher = SwiftWatcher(config)

    tracker = make_tracker(123)
    tracker.ticket = 123
    tracker.symbol = "EURUSD"
    tracker.timeframe = "M15"
    tracker.direction = "bullish"
    tracker.take_profit_1 = 1.1050
    watcher.open_positions[tracker.ticket] = tracker

    mock_mt5 = Mock()
    mock_mt5.get_recent_candles.return_value = [
        {"open": 1.1000, "high": 1.1040, "low": 1.0990, "close": 1.1010, "volume": 1},
        {"open": 1.1010, "high": 1.1020, "low": 1.1000, "close": 1.1005, "volume": 1},
        {"open": 1.1005, "high": 1.1030, "low": 1.1000, "close": 1.1008, "volume": 1},
        {"open": 1.1008, "high": 1.1060, "low": 1.1005, "close": 1.1010, "volume": 1},
    ]

    assert watcher._should_delay_partial_close(tracker, mock_mt5, 1) is False


def test_score_signal_quality_defaults_to_50_on_data_errors(tmp_path):
    """Test that scoring gracefully handles MT5 data failures."""
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1000
    event.stop_loss = 1.0950
    event.take_profit_1 = 1.1050
    
    mock_mt5 = Mock()
    mock_mt5.get_recent_candles.side_effect = RuntimeError("MT5 unavailable")
    
    tick = market_tick(ask=1.1005, bid=1.1004)
    score = watcher._score_signal_quality(event, mock_mt5, tick, 0.0001)
    
    # Should still return a valid score (base 35 minimum)
    assert 0 <= score <= 100
    assert score >= 35  # At minimum, base score


def test_fvg_zone_increases_score(tmp_path):
    """Fair value gap zone should increase signal quality as a confluence boost."""
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)

    event = make_event("EURUSD", "M15", "bullish", "FVG Long Entry")
    event.entry_price = 1.1000
    event.stop_loss = 1.0950
    event.take_profit_1 = 1.1050
    # mark the zone as a fair value gap
    event.zone.zone_type = "fair_value_gap"

    mock_mt5 = Mock()
    mock_mt5.get_recent_candles.return_value = []
    tick = market_tick(ask=1.1005, bid=1.1004)

    score = watcher._score_signal_quality(event, mock_mt5, tick, 0.0001)

    # Base 35 + FVG bonus (8) should lift the score above baseline
    assert score >= 40


def test_fvg_confluence_boosts_premium_zone(tmp_path):
    """When a premium/discount zone also has an FVG confluence flag, it should boost score further."""
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)

    event = make_event("EURUSD", "M15", "bullish", "Premium+FVG Entry")
    event.entry_price = 1.1000
    event.stop_loss = 1.0950
    event.take_profit_1 = 1.1050
    event.zone.zone_type = "premium_zone"
    event.zone.metadata["fvg_confluence"] = True

    mock_mt5 = Mock()
    mock_mt5.get_recent_candles.return_value = []
    tick = market_tick(ask=1.1005, bid=1.1004)

    score = watcher._score_signal_quality(event, mock_mt5, tick, 0.0001)

    assert score >= 42


def test_zone_fairness_rebalances_premium_and_discount_scores(tmp_path):
    """A dominant zone should get a small fairness penalty when the opposite zone has lagged behind."""
    def make_watcher(enable_fairness: bool) -> SwiftWatcher:
        config = ScannerConfig(
            output_dir=tmp_path,
            zone_fairness_enabled=enable_fairness,
            zone_fairness_boost=3.0,
            zone_fairness_min_trades=5,
            zone_fairness_min_win_rate_gap=15.0,
        )
        return SwiftWatcher(config)

    def fake_get_pattern_stats(symbol, timeframe, direction, zone_type):
        if zone_type == "premium_zone":
            return {"total_closed": 6, "wins": 6, "win_rate_pct": 100.0, "total_pnl": 1.25}
        if zone_type == "discount_zone":
            return {"total_closed": 6, "wins": 1, "win_rate_pct": 16.7, "total_pnl": -0.4}
        return {"total_closed": 0, "wins": 0, "win_rate_pct": 0.0, "total_pnl": 0.0}

    fair_watcher = make_watcher(enable_fairness=True)
    fair_watcher.trade_db.get_pattern_stats = Mock(side_effect=fake_get_pattern_stats)

    basic_watcher = make_watcher(enable_fairness=False)
    basic_watcher.trade_db.get_pattern_stats = Mock(side_effect=fake_get_pattern_stats)

    event = make_event("EURUSD", "M15", "bullish", "Premium fairness check")
    event.entry_price = 1.1000
    event.stop_loss = 1.0950
    event.take_profit_1 = 1.1050
    event.zone.zone_type = "premium_zone"

    mock_mt5 = Mock()
    mock_mt5.get_recent_candles.return_value = []
    tick = market_tick(ask=1.1005, bid=1.1004)

    fair_score = fair_watcher._score_signal_quality(event, mock_mt5, tick, 0.0001)
    basic_score = basic_watcher._score_signal_quality(event, mock_mt5, tick, 0.0001)

    assert fair_score < basic_score


def test_fvg_bonus_is_configurable(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    config.zone_gating["fair_value_gap"] = config.zone_gating["fair_value_gap"].__class__(
        entry_timeframes=("M15",),
        require_htf_bias=True,
        max_freshness_closes=3,
        allow_opposite_entry=False,
        min_signal_quality_score=30,
        fvg_bonus=15.0,
    )
    watcher = SwiftWatcher(config)

    event = make_event("EURUSD", "M15", "bullish", "FVG Big Bonus")
    event.entry_price = 1.1000
    event.stop_loss = 1.0950
    event.take_profit_1 = 1.1050
    event.zone.zone_type = "fair_value_gap"

    mock_mt5 = Mock()
    mock_mt5.get_recent_candles.return_value = []
    tick = market_tick(ask=1.1005, bid=1.1004)

    score = watcher._score_signal_quality(event, mock_mt5, tick, 0.0001)

    assert score >= 50


def test_signal_event_decision_fields_round_trip(tmp_path):
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.decision_action = "TRADE"
    event.decision_mood = "CALM"
    event.decision_confidence = 72.0
    event.decision_reason = "quality clears threshold; H1 bias agrees"

    reloaded = SignalEvent.from_dict(event.to_dict())

    assert reloaded.decision_action == "TRADE"
    assert reloaded.decision_mood == "CALM"
    assert reloaded.decision_confidence == 72.0
    assert "H1 bias agrees" in reloaded.decision_reason


def test_trade_reasoning_explains_market_posture(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)
    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1000
    event.stop_loss = 1.0950
    event.take_profit_1 = 1.1050

    mock_mt5 = Mock()
    mock_mt5.get_recent_candles.side_effect = lambda symbol, timeframe, count: (
        [
            {"open": 1.0950, "high": 1.1010, "low": 1.0940, "close": 1.1000, "volume": 1},
            {"open": 1.0960, "high": 1.1020, "low": 1.0950, "close": 1.1010, "volume": 1},
            {"open": 1.0970, "high": 1.1030, "low": 1.0960, "close": 1.1020, "volume": 1},
        ]
        if timeframe.upper() == "M15"
        else [
            {"open": 1.1000, "high": 1.1010, "low": 1.0990, "close": 1.1005, "volume": 1},
            {"open": 1.1005, "high": 1.1020, "low": 1.1000, "close": 1.1010, "volume": 1},
            {"open": 1.1010, "high": 1.1030, "low": 1.1005, "close": 1.1020, "volume": 1},
            {"open": 1.1020, "high": 1.1040, "low": 1.1015, "close": 1.1030, "volume": 1},
            {"open": 1.1030, "high": 1.1050, "low": 1.1025, "close": 1.1040, "volume": 1},
        ]
    )

    reasons = watcher._build_trade_reasoning(
        event,
        mock_mt5,
        market_tick(ask=1.1005, bid=1.1004),
        0.0001,
        75.0,
        25.0,
    )

    assert any("quality 75/100 clears threshold 25" in reason for reason in reasons)
    assert any("M15 momentum" in reason for reason in reasons)
    assert any("TP1 path" in reason for reason in reasons)
    assert any("market feeling" in reason for reason in reasons)


def test_decision_self_talk_uses_human_style_language(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)

    self_talk = watcher._compose_decision_self_talk(
        "TRADE",
        "calm",
        74.0,
        ["quality 74/100 clears threshold 25", "M15 momentum shows 2/3 candles aligned"],
    )

    assert "The setup feels solid enough to act" in self_talk
    assert "My confidence is 74/100" in self_talk


def test_trade_reasoning_includes_thesis_and_risk_points(tmp_path):
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)

    event = make_event("EURUSD", "M15", "bullish", "Main Long Entry")
    event.entry_price = 1.1000
    event.stop_loss = 1.0950
    event.take_profit_1 = 1.1080

    mock_mt5 = Mock()
    mock_mt5.get_recent_candles.return_value = [
        {"open": 1.0950, "high": 1.1010, "low": 1.0940, "close": 1.1000, "volume": 1},
        {"open": 1.0960, "high": 1.1020, "low": 1.0950, "close": 1.1010, "volume": 1},
        {"open": 1.0970, "high": 1.1030, "low": 1.0960, "close": 1.1020, "volume": 1},
    ]

    reasons = watcher._build_trade_reasoning(
        event,
        mock_mt5,
        market_tick(ask=1.1005, bid=1.1004),
        0.0001,
        75.0,
        25.0,
    )

    assert any("where price is likely to go" in reason for reason in reasons)
    assert any("where I’m wrong if it doesn’t" in reason for reason in reasons)
    assert any("how much I’m risking to find out" in reason for reason in reasons)


def test_build_runtime_config_applies_timeframe_risk_configs(tmp_path):
    """Ensure symbols that define `timeframe_risk_configs` use those overrides when
    a matching `timeframe_name` is provided to `_build_runtime_config`.
    """
    config = ScannerConfig(output_dir=tmp_path)
    watcher = SwiftWatcher(config)

    all_symbol_cfgs = build_symbol_scan_configs()
    for symbol, symbol_cfg in all_symbol_cfgs.items():
        if not hasattr(symbol_cfg, "timeframe_risk_configs"):
            continue
        for tf_name, tf_cfg in symbol_cfg.timeframe_risk_configs.items():
            # Build runtime config with the specific timeframe override
            runtime_cfg, raw_overrides, _ = watcher._build_runtime_config(symbol_cfg, timeframe_name=tf_name)

            # For each possible prefix check the value/unit have been propagated
            for prefix in ("main_sl", "main_tp1", "main_tp2", "main_tp3"):
                val_attr = f"{prefix}_value"
                unit_attr = f"{prefix}_unit"
                expected_val = getattr(tf_cfg, val_attr, None)
                expected_unit = getattr(tf_cfg, unit_attr, None)
                if expected_val is None or expected_unit is None:
                    # If timeframe config doesn't specify the field skip
                    continue

                key = prefix
                assert key in raw_overrides, f"{symbol} {tf_name} missing override for {key}"
                raw_val, raw_unit = raw_overrides[key]
                assert pytest.approx(float(expected_val), rel=1e-9) == float(raw_val)
                assert str(raw_unit).lower() == str(expected_unit).lower()
