from __future__ import annotations

from app import build_config_from_args, build_parser
from swift_scanner.config import (
    DEFAULT_TIMEFRAME_NAMES,
    ScannerConfig,
    build_symbol_scan_configs,
    build_timeframes,
    parse_chat_ids,
)


def test_parse_chat_ids_supports_multiple_sources():
    chat_ids = parse_chat_ids("12345, 67890", "67890", "24680;13579")

    assert chat_ids == ["12345", "67890", "24680", "13579"]


def test_default_timeframes_use_fifteen_minute_scans_only():
    assert DEFAULT_TIMEFRAME_NAMES == ["M15"]


def test_build_timeframes_supports_fifteen_minute():
    timeframes = build_timeframes(["M15"])

    assert [tf.name for tf in timeframes] == ["M15"]


def test_parser_defaults_to_two_cent_lots():
    args = build_parser().parse_args([])

    assert args.order_volume == 0.02


def test_parser_defaults_max_confirmation_attempts():
    args = build_parser().parse_args([])

    assert args.max_confirmation_attempts == 5


def test_ustec_defaults_use_wider_sl_tp_levels():
    configs = build_symbol_scan_configs()
    nasdaq_config = configs["NASDAQ"]

    assert nasdaq_config.main_sl_value == 88.0
    assert nasdaq_config.main_tp1_value == 60.0
    assert nasdaq_config.main_tp2_value == 120.0
    assert nasdaq_config.timeframe_risk_configs["M1"].main_sl_value == 27.5
    assert nasdaq_config.timeframe_risk_configs["M5"].main_sl_value == 55.0
    assert nasdaq_config.timeframe_risk_configs["M15"].main_sl_value == 33.0
    assert nasdaq_config.timeframe_risk_configs["M15"].main_tp1_value == 42.0
    assert nasdaq_config.timeframe_risk_configs["M15"].main_tp2_value == 84.0


def test_usoil_defaults_use_wider_stop_loss():
    configs = build_symbol_scan_configs()
    usoil_config = configs["USOIL"]

    assert usoil_config.main_sl_value == 0.1375
    assert usoil_config.timeframe_risk_configs["M1"].main_sl_value == 0.20625
    assert usoil_config.timeframe_risk_configs["M5"].main_sl_value == 0.462
    assert usoil_config.timeframe_risk_configs["M15"].main_sl_value == 0.1375


def test_parser_defaults_to_tp3_target_for_partial_close_ladder():
    args = build_parser().parse_args([])

    assert args.tp_target_level == "tp3"


def test_parser_supports_disable_killzone_filter():
    args = build_parser().parse_args(["--disable-killzone-filter"])

    assert args.disable_killzone_filter is True


def test_build_config_from_args_honors_disable_killzone_filter():
    args = build_parser().parse_args(["--disable-killzone-filter"])
    config = build_config_from_args(args)

    assert config.disable_killzone_filter is True


def test_scanner_config_reads_disable_killzone_filter_from_env(monkeypatch):
    monkeypatch.setenv("DISABLE_KILLZONE_FILTER", "true")
    config = ScannerConfig()

    assert config.disable_killzone_filter is True


def test_parser_invert_indices_sets_named_index_symbols():
    args = build_parser().parse_args(["--invert-indices"])

    assert args.invert_indices is True
    assert args.invert_execution_for == ""
