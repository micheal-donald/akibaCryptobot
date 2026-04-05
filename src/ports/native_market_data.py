"""Native market-data port contracts for the hot-path boundary."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .exchange import MarketDataGateway


@runtime_checkable
class NativeMarketDataGateway(MarketDataGateway, Protocol):
    """Native hot-path contract for market-data normalization."""

    pass
