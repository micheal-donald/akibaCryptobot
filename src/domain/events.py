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
    "signal.order_intent",
    "execution.order_submitted",
    "execution.order_update",
    "execution.fill",
    "execution.order_rejected",
    "execution.balance_update",
    "position.opened",
    "position.protective_submitted",
    "position.closed",
    "risk.blocked",
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


@dataclass(slots=True, frozen=True)
class OrderIntentPayload:
    """Execution request emitted by signal or strategy layers."""

    intent_id: str
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"]
    quantity: float
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    reduce_only: bool = False
    strategy: str | None = None
    reason: str | None = None


@dataclass(slots=True, frozen=True)
class OrderSubmittedPayload:
    """Submission acknowledgement from the execution service or adapter."""

    client_order_id: str
    exchange_order_id: str | None
    symbol: str
    side: Literal["buy", "sell"]
    order_type: str
    requested_qty: float
    requested_price: float | None = None
    reduce_only: bool = False


@dataclass(slots=True, frozen=True)
class OrderUpdatePayload:
    """Normalized order lifecycle update from the exchange."""

    client_order_id: str | None
    exchange_order_id: str | None
    symbol: str
    status: str
    filled_qty: float
    remaining_qty: float
    avg_fill_price: float | None = None
    reduce_only: bool = False
    raw_status: str | None = None


@dataclass(slots=True, frozen=True)
class FillPayload:
    """Normalized trade fill update."""

    client_order_id: str | None
    exchange_order_id: str | None
    symbol: str
    side: Literal["buy", "sell"]
    fill_qty: float
    fill_price: float
    cumulative_qty: float
    cumulative_quote_qty: float | None = None
    fee_cost: float | None = None
    fee_currency: str | None = None
    is_maker: bool | None = None
    reduce_only: bool = False


@dataclass(slots=True, frozen=True)
class BalanceUpdatePayload:
    """Normalized balance update from the exchange user stream."""

    balances: dict[str, dict[str, float]]


@dataclass(slots=True, frozen=True)
class PositionOpenedPayload:
    """Event emitted when a position becomes open."""

    position_id: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: float
    avg_entry_price: float
    entry_order_ids: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class PositionClosedPayload:
    """Event emitted when a position becomes closed."""

    position_id: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: float
    avg_entry_price: float | None
    avg_exit_price: float | None
    close_reason: str
    pnl: float | None = None
    fee_cost: float | None = None


@dataclass(slots=True, frozen=True)
class RiskBlockedPayload:
    """Risk-service decision explaining why an order intent was blocked."""

    intent_id: str
    symbol: str
    reason: str
