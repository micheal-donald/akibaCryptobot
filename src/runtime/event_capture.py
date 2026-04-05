"""Bounded runtime event capture helpers for debugging and tests."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
import json

from domain.events import RuntimeEvent


def _serialize_capture_value(value: object) -> object:
    """Convert runtime payload values into JSON-safe capture data."""

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
class CapturedRuntimeEvent:
    """Stable serialized capture record for a runtime event."""

    capture_index: int
    event_id: str
    event_type: str
    source: str
    symbol: str | None
    ts_exchange: str | None
    ts_received: str
    captured_at: str
    payload_type: str
    payload: object

    @classmethod
    def from_event(
        cls,
        event: RuntimeEvent,
        *,
        capture_index: int,
        captured_at: datetime,
    ) -> "CapturedRuntimeEvent":
        payload = event.payload
        return cls(
            capture_index=capture_index,
            event_id=event.meta.event_id,
            event_type=event.meta.event_type,
            source=event.meta.source,
            symbol=event.meta.symbol,
            ts_exchange=(
                event.meta.ts_exchange.isoformat()
                if event.meta.ts_exchange is not None
                else None
            ),
            ts_received=event.meta.ts_received.isoformat(),
            captured_at=captured_at.isoformat(),
            payload_type=type(payload).__name__,
            payload=_serialize_capture_value(payload),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "capture_index": self.capture_index,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "symbol": self.symbol,
            "ts_exchange": self.ts_exchange,
            "ts_received": self.ts_received,
            "captured_at": self.captured_at,
            "payload_type": self.payload_type,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), separators=(",", ":"), sort_keys=True)


@dataclass(slots=True)
class RuntimeEventCaptureBuffer:
    """Bounded in-memory ring buffer for serialized runtime event capture."""

    max_records: int = 256
    _records: deque[CapturedRuntimeEvent] = field(init=False, repr=False)
    _next_index: int = field(default=1, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_records <= 0:
            raise ValueError("max_records must be positive")
        self._records = deque(maxlen=self.max_records)

    def capture(
        self,
        event: RuntimeEvent,
        *,
        captured_at: datetime,
    ) -> CapturedRuntimeEvent:
        record = CapturedRuntimeEvent.from_event(
            event,
            capture_index=self._next_index,
            captured_at=captured_at,
        )
        self._records.append(record)
        self._next_index += 1
        return record

    def get_records(self) -> tuple[CapturedRuntimeEvent, ...]:
        return tuple(self._records)

    def get_serialized_records(self) -> tuple[dict[str, object], ...]:
        return tuple(record.as_dict() for record in self._records)

    def clear(self) -> None:
        self._records.clear()

    def __len__(self) -> int:
        return len(self._records)
