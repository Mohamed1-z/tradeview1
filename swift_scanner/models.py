from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Any
from swift_scanner.config import TIMEFRAME_MAP


ZoneType = Literal[
    "swift_signal",
    "fair_value_gap",
    "liquidity_sweep",
    "premium_zone",
    "discount_zone",
]
SignalDirection = Literal["bullish", "bearish"]
SignalSource = Literal["swift", "zone"]


@dataclass(slots=True)
class Zone:
    zone_type: ZoneType
    direction: SignalDirection
    symbol_request: str
    symbol_actual: str
    created_at: datetime
    left_index: int
    right_index: int
    top: float
    bottom: float
    formed_index: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def key(self) -> str:
        return (
            f"{self.symbol_actual}|{self.zone_type}|"
            f"{self.direction}|{self.created_at.isoformat()}|"
            f"{self.top:.8f}|{self.bottom:.8f}"
        )


@dataclass(slots=True)
class SignalEvent:
    symbol_request: str
    symbol_actual: str
    timeframe: str
    triggered_at: datetime
    price: float
    zone: Zone
    signal_source: SignalSource = "swift"
    signal_label: str = ""
    explanation: str = ""
    lesson: str = ""
    signal_tags: list[str] = field(default_factory=list)
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    take_profit_1: float | None = None
    take_profit_2: float | None = None
    take_profit_3: float | None = None
    risk_reward_ratio: float | None = None
    order_ticket: int | None = None
    image_path: Path | None = None
    market_change_alert: bool = False
    decision_action: str = ""
    decision_mood: str = ""
    decision_confidence: float | None = None
    decision_reason: str = ""
    decision_self_talk: str = ""

    @property
    def event_id(self) -> str:
        return (
            f"{self.symbol_actual}|{self.timeframe}|{self.signal_source}|"
            f"{self.signal_label}|{self.zone.key}"
        )

    @property
    def is_early_signal(self) -> bool:
        """Check if signal is early (entry hasn't moved >50% toward TP1)."""
        if (
            self.entry_price is None
            or self.take_profit_1 is None
            or self.stop_loss is None
        ):
            return False
        
        # Calculate distance from SL to TP1
        if self.zone.direction == "bullish":
            total_distance = self.take_profit_1 - self.stop_loss
            current_move = self.entry_price - self.stop_loss
            # Early if we haven't moved 50% from SL toward TP1
            return current_move < (total_distance * 0.5)
        else:  # bearish
            total_distance = self.stop_loss - self.take_profit_1
            current_move = self.stop_loss - self.entry_price
            # Early if we haven't moved 50% from SL toward TP1
            return current_move < (total_distance * 0.5)

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "symbol_request": self.symbol_request,
            "symbol_actual": self.symbol_actual,
            "timeframe": self.timeframe,
            "triggered_at": self.triggered_at.isoformat(),
            "price": self.price,
            "zone_type": self.zone.zone_type,
            "signal_direction": self.zone.direction,
            "zone_top": self.zone.top,
            "zone_bottom": self.zone.bottom,
            "zone_created_at": self.zone.created_at.isoformat(),
            "zone_formed_index": self.zone.formed_index,
            "signal_source": self.signal_source,
            "signal_label": self.signal_label,
            "explanation": self.explanation,
            "lesson": self.lesson,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "take_profit_3": self.take_profit_3,
            "risk_reward_ratio": self.risk_reward_ratio,
            "order_ticket": self.order_ticket,
            "image_path": str(self.image_path) if self.image_path is not None else None,
            "signal_tags": self.signal_tags,
            "market_change_alert": self.market_change_alert,
            "decision_action": self.decision_action,
            "decision_mood": self.decision_mood,
            "decision_confidence": self.decision_confidence,
            "decision_reason": self.decision_reason,
            "decision_self_talk": self.decision_self_talk,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "SignalEvent":
        from datetime import datetime

        direction = str(
            payload.get("signal_direction")
            or payload.get("direction")
            or payload.get("zone_direction")
            or ""
        ).lower()
        if direction not in {"bullish", "bearish"}:
            label = str(payload.get("signal_label", "")).lower()
            direction = (
                "bullish"
                if "bull" in label or "long" in label or "buy" in label
                else "bearish"
            )

        zone = Zone(
            zone_type=payload.get("zone_type", "swift_signal"),
            direction=direction,
            symbol_request=payload["symbol_request"],
            symbol_actual=payload["symbol_actual"],
            created_at=datetime.fromisoformat(payload["zone_created_at"]),
            left_index=0,
            right_index=0,
            top=float(payload["zone_top"]),
            bottom=float(payload["zone_bottom"]),
            formed_index=(
                int(payload["zone_formed_index"])
                if payload.get("zone_formed_index") is not None
                else None
            ),
        )
        image_path = payload.get("image_path")
        # Normalize timeframe values passed in from external payloads
        def _normalize_timeframe_local(tf: object) -> str:
            # reverse lookup numeric MT5 codes if possible
            try:
                if isinstance(tf, int):
                    rev = {v: k for k, v in TIMEFRAME_MAP.items()}
                    if tf in rev:
                        return rev[tf]
            except Exception:
                pass
            normalized = str(tf).strip().upper()
            aliases = {
                "15M": "M15",
                "M15": "M15",
                "30M": "M30",
                "M30": "M30",
                "1H": "H1",
                "H1": "H1",
                "4H": "H4",
                "H4": "H4",
                "1D": "D1",
                "D1": "D1",
                "D": "D1",
            }
            if normalized in aliases:
                return aliases[normalized]
            if normalized.isdigit():
                n = int(normalized)
                if n == 15:
                    return "M15"
                if n == 30:
                    return "M30"
                if n == 60:
                    return "H1"
                if n == 240:
                    return "H4"
                if n == 1:
                    return "D1"
            return normalized

        tf_value = payload.get("timeframe")
        tf_value = _normalize_timeframe_local(tf_value)

        return cls(
            symbol_request=payload["symbol_request"],
            symbol_actual=payload["symbol_actual"],
            timeframe=tf_value,
            triggered_at=datetime.fromisoformat(payload["triggered_at"]),
            price=float(payload["price"]),
            zone=zone,
            signal_source=payload.get("signal_source", "swift"),
            signal_label=str(payload.get("signal_label", "")),
            entry_price=(
                float(payload["entry_price"])
                if payload.get("entry_price") is not None
                else None
            ),
            stop_loss=(
                float(payload["stop_loss"]) if payload.get("stop_loss") is not None else None
            ),
            take_profit=(
                float(payload["take_profit"])
                if payload.get("take_profit") is not None
                else None
            ),
            take_profit_1=(
                float(payload["take_profit_1"])
                if payload.get("take_profit_1") is not None
                else None
            ),
            take_profit_2=(
                float(payload["take_profit_2"])
                if payload.get("take_profit_2") is not None
                else None
            ),
            take_profit_3=(
                float(payload["take_profit_3"])
                if payload.get("take_profit_3") is not None
                else None
            ),
            risk_reward_ratio=(
                float(payload["risk_reward_ratio"])
                if payload.get("risk_reward_ratio") is not None
                else None
            ),
            order_ticket=(
                int(payload["order_ticket"])
                if payload.get("order_ticket") is not None
                else None
            ),
            signal_tags=[
                str(tag).strip()
                for tag in payload.get("signal_tags", [])
                if tag is not None and str(tag).strip()
            ],
            market_change_alert=bool(payload.get("market_change_alert", False)),
            decision_action=str(payload.get("decision_action", "")),
            decision_mood=str(payload.get("decision_mood", "")),
            decision_confidence=(
                float(payload["decision_confidence"])
                if payload.get("decision_confidence") is not None
                else None
            ),
            decision_reason=str(payload.get("decision_reason", "")),
            decision_self_talk=str(payload.get("decision_self_talk", "")),
            explanation=str(payload.get("explanation", "")),
            lesson=str(payload.get("lesson", payload.get("explanation", ""))),
            image_path=Path(image_path) if image_path else None,
        )


@dataclass(slots=True)
class ScanResult:
    symbol_request: str
    symbol_actual: str
    timeframe: str
    bars: Any
    zones: list[Zone]
    fresh_events: list[SignalEvent]
    latest_close: float
    last_bar_time: datetime
