"""Bounded capture helpers for order-book and microstructure replay."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
import json

from services.order_book_service import OrderBookSnapshot


def _serialize_capture_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {
            key: _serialize_capture_value(item) for key, item in asdict(value).items()
        }
    if isinstance(value, Mapping):
        return {str(key): _serialize_capture_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [_serialize_capture_value(item) for item in value]
    return repr(value)


@dataclass(slots=True, frozen=True)
class CapturedDepthEvent:
    """Stable serialized capture record for depth and trade events."""

    capture_index: int
    event_id: str
    event_type: str
    source: str
    symbol: str | None
    sequence: int | None
    ts_exchange: str | None
    ts_received: str
    captured_at: str
    payload_type: str
    payload: object

    @classmethod
    def from_order_book_snapshot(
        cls,
        snapshot: OrderBookSnapshot,
        *,
        capture_index: int,
        event_id: str,
        event_type: str,
        source: str,
        captured_at: datetime,
    ) -> "CapturedDepthEvent":
        payload = {
            "symbol": snapshot.symbol,
            "bids": [
                {"price": level.price, "quantity": level.quantity}
                for level in snapshot.bids
            ],
            "asks": [
                {"price": level.price, "quantity": level.quantity}
                for level in snapshot.asks
            ],
            "best_bid": (
                None
                if snapshot.best_bid is None
                else {
                    "price": snapshot.best_bid.price,
                    "quantity": snapshot.best_bid.quantity,
                }
            ),
            "best_ask": (
                None
                if snapshot.best_ask is None
                else {
                    "price": snapshot.best_ask.price,
                    "quantity": snapshot.best_ask.quantity,
                }
            ),
            "mid_price": snapshot.mid_price,
            "spread": snapshot.spread,
            "sequence": snapshot.sequence,
            "is_stale": snapshot.is_stale,
            "is_gap_detected": snapshot.is_gap_detected,
            "is_sequence_valid": snapshot.is_sequence_valid,
            "last_sequence_delta": snapshot.last_sequence_delta,
            "sequence_issue": snapshot.sequence_issue,
            "exchange_timestamp": (
                snapshot.exchange_timestamp.isoformat()
                if snapshot.exchange_timestamp is not None
                else None
            ),
            "received_at": (
                snapshot.received_at.isoformat()
                if snapshot.received_at is not None
                else None
            ),
            "updated_at": snapshot.updated_at.isoformat(),
        }
        return cls(
            capture_index=capture_index,
            event_id=event_id,
            event_type=event_type,
            source=source,
            symbol=snapshot.symbol,
            sequence=snapshot.sequence,
            ts_exchange=(
                snapshot.exchange_timestamp.isoformat()
                if snapshot.exchange_timestamp is not None
                else None
            ),
            ts_received=(
                snapshot.received_at.isoformat()
                if snapshot.received_at is not None
                else captured_at.isoformat()
            ),
            captured_at=captured_at.isoformat(),
            payload_type="OrderBookSnapshot",
            payload=_serialize_capture_value(payload),
        )

    @classmethod
    def from_trade_event(
        cls,
        *,
        capture_index: int,
        event_id: str,
        event_type: str,
        source: str,
        symbol: str | None,
        side: str,
        price: float,
        quantity: float,
        sequence: int | None,
        ts_exchange: datetime | None,
        ts_received: datetime,
        captured_at: datetime,
        extra: Mapping[str, object] | None = None,
    ) -> "CapturedDepthEvent":
        payload: dict[str, object] = {
            "symbol": symbol,
            "side": side,
            "price": price,
            "quantity": quantity,
            "sequence": sequence,
        }
        if extra is not None:
            payload.update(dict(extra))
        return cls(
            capture_index=capture_index,
            event_id=event_id,
            event_type=event_type,
            source=source,
            symbol=symbol,
            sequence=sequence,
            ts_exchange=ts_exchange.isoformat() if ts_exchange is not None else None,
            ts_received=ts_received.isoformat(),
            captured_at=captured_at.isoformat(),
            payload_type="TradeEvent",
            payload=_serialize_capture_value(payload),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "capture_index": self.capture_index,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "symbol": self.symbol,
            "sequence": self.sequence,
            "ts_exchange": self.ts_exchange,
            "ts_received": self.ts_received,
            "captured_at": self.captured_at,
            "payload_type": self.payload_type,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), separators=(",", ":"), sort_keys=True)


@dataclass(slots=True)
class DepthCaptureBuffer:
    """Bounded in-memory capture ring buffer for depth/trade records."""

    max_records: int = 512
    _records: deque[CapturedDepthEvent] = field(init=False, repr=False)
    _next_index: int = field(default=1, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_records <= 0:
            raise ValueError("max_records must be positive")
        self._records = deque(maxlen=self.max_records)

    def capture_snapshot(
        self,
        snapshot: OrderBookSnapshot,
        *,
        event_id: str,
        event_type: str,
        source: str,
        captured_at: datetime,
    ) -> CapturedDepthEvent:
        record = CapturedDepthEvent.from_order_book_snapshot(
            snapshot,
            capture_index=self._next_index,
            event_id=event_id,
            event_type=event_type,
            source=source,
            captured_at=captured_at,
        )
        self._records.append(record)
        self._next_index += 1
        return record

    def capture_trade(
        self,
        *,
        event_id: str,
        event_type: str,
        source: str,
        symbol: str | None,
        side: str,
        price: float,
        quantity: float,
        sequence: int | None,
        ts_exchange: datetime | None,
        ts_received: datetime,
        captured_at: datetime,
        extra: Mapping[str, object] | None = None,
    ) -> CapturedDepthEvent:
        record = CapturedDepthEvent.from_trade_event(
            capture_index=self._next_index,
            event_id=event_id,
            event_type=event_type,
            source=source,
            symbol=symbol,
            side=side,
            price=price,
            quantity=quantity,
            sequence=sequence,
            ts_exchange=ts_exchange,
            ts_received=ts_received,
            captured_at=captured_at,
            extra=extra,
        )
        self._records.append(record)
        self._next_index += 1
        return record

    def get_records(self) -> tuple[CapturedDepthEvent, ...]:
        return tuple(self._records)

    def to_json_lines(self) -> tuple[str, ...]:
        return tuple(record.to_json() for record in self._records)

    def write_jsonl(self, path: str | Path) -> Path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "".join(f"{line}\n" for line in self.to_json_lines()),
            encoding="utf-8",
        )
        return output_path

    def clear(self) -> None:
        self._records.clear()

    def __len__(self) -> int:
        return len(self._records)
