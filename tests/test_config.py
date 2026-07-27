from __future__ import annotations

from app import _ask_detectors, build_config_from_args, build_parser
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


def test_default_timeframes_use_layered_bias_flow():
    assert DEFAULT_TIMEFRAME_NAMES == ["H4", "H2", "H1", "M15"]


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

    assert nasdaq_config.main_sl_value == 105.6
    assert nasdaq_config.main_tp1_value == 60.0
    assert nasdaq_config.main_tp2_value == 120.0
    assert nasdaq_config.timeframe_risk_configs["M1"].main_sl_value == 33.0
    assert nasdaq_config.timeframe_risk_configs["M5"].main_sl_value == 66.0
    assert nasdaq_config.timeframe_risk_configs["M15"].main_sl_value == 39.6
    assert nasdaq_config.timeframe_risk_configs["M15"].main_tp1_value == 42.0
    assert nasdaq_config.timeframe_risk_configs["M15"].main_tp2_value == 84.0


def test_usoil_defaults_use_wider_stop_loss():
    configs = build_symbol_scan_configs()
    usoil_config = configs["USOIL"]

    assert usoil_config.main_sl_value == 0.72
    assert usoil_config.main_tp1_value == 0.525
    assert usoil_config.main_tp2_value == 1.05
    assert usoil_config.main_tp3_value == 1.8
    assert usoil_config.timeframe_risk_configs["M1"].main_sl_value == 1.125
    assert usoil_config.timeframe_risk_configs["M5"].main_sl_value == 2.52
    assert usoil_config.timeframe_risk_configs["M15"].main_sl_value == 0.72


def test_us500_reduced_risk_settings():
    configs = build_symbol_scan_configs()
    us500_config = configs["US500"]

    assert us500_config.main_sl_value == 90.0
    assert us500_config.reduce_position_fraction == 0.2
    assert us500_config.entry_buffer_min == 7.5
    assert us500_config.entry_buffer_max == 15.0


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


def test_discount_zone_gating_is_stricter_by_default():
    config = ScannerConfig()
    discount_cfg = config.zone_gating["discount_zone"]

    assert discount_cfg.min_signal_quality_score == 50
    assert discount_cfg.max_freshness_closes == 3


def test_parser_supports_equity_based_sizing_flags():
    args = build_parser().parse_args(["--use-equity-based-sizing", "--risk-percent-per-trade", "2.5"])
    config = build_config_from_args(args)

    assert config.use_equity_based_sizing is True
    assert config.risk_percent_per_trade == 2.5


def test_parser_supports_detection_and_gate_startup_overrides():
    args = build_parser().parse_args(["--disable-zones", "--bypass-execution-gates", "--no-startup-prompt"])

    assert args.disable_zones is True
    assert args.bypass_execution_gates is True
    assert args.no_startup_prompt is True


def test_build_config_from_args_honors_detection_and_gate_overrides():
    args = build_parser().parse_args(["--disable-swift", "--bypass-execution-gates"])
    config = build_config_from_args(args)

    assert config.enable_swift is False
    assert config.main_enabled is False
    assert config.enable_zone_trading is True
    assert config.bypass_execution_gates is True


def test_startup_detector_prompt_supports_numbered_zone_selection(monkeypatch):
    config = ScannerConfig(enable_swift=True, enable_zone_trading=True)
    monkeypatch.setattr("builtins.input", lambda _: "2,4")

    enable_swift, selected_zones = _ask_detectors(config)

    assert enable_swift is False
    assert selected_zones == ["fair_value_gap", "premium_zone"]


def test_startup_detector_prompt_supports_names_and_swift(monkeypatch):
    config = ScannerConfig(enable_swift=True, enable_zone_trading=True)
    monkeypatch.setattr("builtins.input", lambda _: "swift liquidity_sweep discount")

    enable_swift, selected_zones = _ask_detectors(config)

    assert enable_swift is True
    assert selected_zones == ["liquidity_sweep", "discount_zone"]


def test_build_config_from_args_can_disable_zone_detection():
    args = build_parser().parse_args(["--disable-zones"])
    config = build_config_from_args(args)

    assert config.enable_swift is True
    assert config.enable_zone_trading is False


def test_scanner_config_reads_disable_killzone_filter_from_env(monkeypatch):
    monkeypatch.setenv("DISABLE_KILLZONE_FILTER", "true")
    config = ScannerConfig()

    assert config.disable_killzone_filter is True


def test_parser_invert_indices_sets_named_index_symbols():
    args = build_parser().parse_args(["--invert-indices"])

    assert args.invert_indices is True
    assert args.invert_execution_for == ""
