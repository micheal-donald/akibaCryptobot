"""Runtime event contracts for the phase 1 event-driven runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

EventType = Literal[
    "market.tick",
    "market.kline",
    "market.kline_closed",
    "market.spread",
    "timer.housekeeping",
    "runtime.shutdown",
]


@dataclass(slots=True, frozen=True)
class EventMeta:
    """Metadata shared by all runtime events."""

    event_id: str
    event_type: EventType
    source: str
    symbol: str | None
    ts_exchange: datetime | None
    ts_received: datetime


@dataclass(slots=True, frozen=True)
class RuntimeEvent:
    """Normalized event envelope passed through the runtime."""

    meta: EventMeta
    payload: object


@dataclass(slots=True, frozen=True)
class BookTickerPayload:
    """Best bid/ask snapshot from the exchange."""

    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float


@dataclass(slots=True, frozen=True)
class KlinePayload:
    """OHLCV update from the exchange."""

    interval: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool


@dataclass(slots=True, frozen=True)
class HousekeepingPayload:
    """Payload for timer-driven maintenance work."""

    reason: str
