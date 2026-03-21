"""Exchange port contracts used by the runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal, Protocol, runtime_checkable

from domain.events import RuntimeEvent

MarketDataEventType = Literal[
    "market.tick",
    "market.kline",
    "market.kline_closed",
    "market.spread",
]

MarketDataEventHandler = Callable[[RuntimeEvent], Awaitable[None] | None]


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
