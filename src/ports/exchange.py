"""Exchange port contracts used by the runtime."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol, runtime_checkable

from domain.events import (
    OrderIntentPayload,
    OrderSubmittedPayload,
    OrderUpdatePayload,
    RuntimeEvent,
)
from domain.models import Candle

MarketDataEventType = Literal[
    "market.tick",
    "market.kline",
    "market.kline_closed",
    "market.spread",
    "market.depth",
    "market.trade",
]

MarketDataEventHandler = Callable[[RuntimeEvent], Awaitable[None] | None]
ExecutionEventType = Literal[
    "execution.order_submitted",
    "execution.order_update",
    "execution.fill",
    "execution.order_rejected",
    "execution.balance_update",
    "position.opened",
    "position.protective_submitted",
    "position.closed",
]
ExecutionEventHandler = Callable[[RuntimeEvent], Awaitable[None] | None]
UserStreamEventType = Literal[
    "execution.order_update",
    "execution.fill",
    "execution.balance_update",
]
UserStreamEventHandler = ExecutionEventHandler


@dataclass(slots=True, frozen=True)
class ProtectionIntent:
    """Exchange-side protection request for an open position."""

    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    trailing_stop_price: float | None = None
    native_preferred: bool = True


@runtime_checkable
class MarketDataGateway(Protocol):
    """Narrow market-data gateway used by the runtime shell."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    def register_callback(
        self, event_type: MarketDataEventType, callback: MarketDataEventHandler
    ) -> None: ...

    async def subscribe_book_ticker(self, symbol: str) -> None: ...

    async def subscribe_kline(self, symbol: str, interval: str = "1m") -> None: ...


@runtime_checkable
class DepthStreamGateway(Protocol):
    """Optional depth/trade stream surface for microstructure consumers.

    Kept separate from MarketDataGateway so existing gateway fakes remain
    structurally valid; adapters that support live depth implement both.
    """

    def register_callback(
        self, event_type: MarketDataEventType, callback: MarketDataEventHandler
    ) -> None: ...

    async def subscribe_depth(self, symbol: str, depth: int = 10) -> None: ...

    async def subscribe_trade(self, symbol: str) -> None: ...


@runtime_checkable
class ExecutionGateway(Protocol):
    """Execution-facing gateway for order submission and lifecycle queries."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def submit_order(
        self, intent: OrderIntentPayload
    ) -> OrderSubmittedPayload: ...

    async def cancel_order(
        self, symbol: str, exchange_order_id: str
    ) -> OrderUpdatePayload: ...

    async def fetch_order(
        self, symbol: str, exchange_order_id: str
    ) -> OrderUpdatePayload: ...

    async def submit_protection(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        quantity: float,
        protection: ProtectionIntent,
    ) -> tuple[OrderSubmittedPayload, ...]: ...


@runtime_checkable
class UserStreamGateway(Protocol):
    """User-stream gateway for order, fill, and balance updates."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def subscribe_user_stream(self) -> None: ...

    def register_order_update_handler(
        self, handler: UserStreamEventHandler
    ) -> None: ...

    def register_fill_handler(self, handler: UserStreamEventHandler) -> None: ...

    def register_balance_update_handler(
        self, handler: UserStreamEventHandler
    ) -> None: ...


@runtime_checkable
class MarketDataBootstrapGateway(Protocol):
    """REST bootstrap contract for historical OHLCV loading."""

    async def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        limit: int,
    ) -> list[Candle]: ...
