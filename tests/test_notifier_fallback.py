import pytest
from unittest.mock import patch
from pathlib import Path
from datetime import datetime
import requests

from swift_scanner.notifier import SignalNotifier
from swift_scanner.config import ScannerConfig
from swift_scanner.models import SignalEvent, Zone


def make_config(tmp_path: Path) -> ScannerConfig:
    cfg = ScannerConfig()
    cfg.telegram_bot_token = "fake-token"
    cfg.telegram_chat_ids = ["1234"]
    cfg.telegram_silent = False
    cfg.output_dir = tmp_path
    return cfg


def make_event() -> SignalEvent:
    zone = Zone(
        zone_type="swift_signal",
        direction="bullish",
        symbol_request="TEST",
        symbol_actual="TEST",
        created_at=datetime.utcnow(),
        left_index=0,
        right_index=1,
        top=1.0,
        bottom=0.5,
    )
    ev = SignalEvent(
        symbol_request="TEST",
        symbol_actual="TEST",
        timeframe="M5",
        triggered_at=datetime.utcnow(),
        price=1.2345,
        zone=zone,
    )
    ev.entry_price = 1.2345
    ev.stop_loss = 1.2300
    ev.take_profit_1 = 1.2400
    ev.take_profit_2 = 1.2450
    ev.take_profit_3 = 1.2500
    return ev


@patch("swift_scanner.notifier.requests.Session.post")
def test_telegram_image_fallback_to_text_and_document(mock_post, tmp_path):
    from requests.exceptions import SSLError

    # First image upload fails with SSLError, text-only fallback also fails, document fallback raises
    mock_post.side_effect = SSLError("EOF occurred in violation of protocol")

    cfg = make_config(tmp_path)
    notifier = SignalNotifier(cfg)
    ev = make_event()
    img = tmp_path / "img.png"
    img.write_bytes(b"PNGDATA")

    with pytest.raises(RuntimeError):
        notifier.send_telegram(ev, img)

    fallback = cfg.output_dir / "telegram_fallback.jsonl"
    assert fallback.exists()
    content = fallback.read_text()
    assert "telegram_failure_reason" in content
