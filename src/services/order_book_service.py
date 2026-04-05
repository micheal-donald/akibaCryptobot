"""In-memory local order-book state for low-latency runtime research."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

from .base import BaseService


@dataclass(slots=True, frozen=True)
class OrderBookLevel:
    """Single normalized order-book level."""

    price: float
    quantity: float


@dataclass(slots=True, frozen=True)
class OrderBookSnapshot:
    """Immutable snapshot of a symbol's local order book."""

    symbol: str
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    sequence: int | None
    exchange_timestamp: datetime | None
    received_at: datetime | None
    updated_at: datetime

    @property
    def best_bid(self) -> OrderBookLevel | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> OrderBookLevel | None:
        return self.asks[0] if self.asks else None

    @property
    def mid_price(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid.price + self.best_ask.price) / 2

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask.price - self.best_bid.price


@dataclass(slots=True)
class _OrderBookState:
    """Mutable depth state for a single symbol."""

    symbol: str
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    sequence: int | None = None
    exchange_timestamp: datetime | None = None
    received_at: datetime | None = None
    updated_at: datetime | None = None

    def replace_snapshot(
        self,
        bids: Sequence[OrderBookLevel],
        asks: Sequence[OrderBookLevel],
        *,
        sequence: int | None = None,
        exchange_timestamp: datetime | None = None,
        received_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        self.bids = {
            level.price: level.quantity for level in bids if level.quantity > 0
        }
        self.asks = {
            level.price: level.quantity for level in asks if level.quantity > 0
        }
        self.sequence = sequence
        self.exchange_timestamp = exchange_timestamp
        self.received_at = received_at
        self.updated_at = updated_at or received_at or datetime.now(timezone.utc)

    def apply_level_update(
        self,
        side: str,
        price: float,
        quantity: float,
        *,
        sequence: int | None = None,
        exchange_timestamp: datetime | None = None,
        received_at: datetime | None = None,
    ) -> None:
        levels = self._levels_for_side(side)
        if quantity <= 0:
            levels.pop(price, None)
        else:
            levels[price] = quantity
        self.sequence = sequence if sequence is not None else self.sequence
        self.exchange_timestamp = exchange_timestamp or self.exchange_timestamp
        self.received_at = received_at or self.received_at
        self.updated_at = received_at or datetime.now(timezone.utc)

    def snapshot(self, *, depth: int | None = None) -> OrderBookSnapshot:
        depth = None if depth is None or depth <= 0 else depth
        bids = tuple(
            OrderBookLevel(price=price, quantity=self.bids[price])
            for price in self._sorted_bids()[:depth]
        )
        asks = tuple(
            OrderBookLevel(price=price, quantity=self.asks[price])
            for price in self._sorted_asks()[:depth]
        )
        return OrderBookSnapshot(
            symbol=self.symbol,
            bids=bids,
            asks=asks,
            sequence=self.sequence,
            exchange_timestamp=self.exchange_timestamp,
            received_at=self.received_at,
            updated_at=self.updated_at or datetime.now(timezone.utc),
        )

    def _levels_for_side(self, side: str) -> dict[float, float]:
        normalized = side.lower()
        if normalized in {"bid", "bids", "buy"}:
            return self.bids
        if normalized in {"ask", "asks", "sell"}:
            return self.asks
        raise ValueError(f"unsupported order-book side: {side!r}")

    def _sorted_bids(self) -> list[float]:
        return sorted(self.bids, reverse=True)

    def _sorted_asks(self) -> list[float]:
        return sorted(self.asks)


@dataclass(slots=True)
class OrderBookService(BaseService):
    """Owns normalized local order-book state per symbol."""

    default_depth_window: int = 5
    _states: dict[str, _OrderBookState] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        BaseService.__post_init__(self)
        if self.default_depth_window <= 0:
            raise ValueError("default_depth_window must be positive")

    async def on_start(self) -> None:
        return None

    async def on_stop(self) -> None:
        return None

    def replace_snapshot(
        self,
        symbol: str,
        *,
        bids: Sequence[OrderBookLevel | Mapping[str, float] | tuple[float, float]],
        asks: Sequence[OrderBookLevel | Mapping[str, float] | tuple[float, float]],
        sequence: int | None = None,
        exchange_timestamp: datetime | None = None,
        received_at: datetime | None = None,
    ) -> OrderBookSnapshot:
        state = self._states.setdefault(symbol, _OrderBookState(symbol=symbol))
        state.replace_snapshot(
            tuple(self._coerce_levels(bids)),
            tuple(self._coerce_levels(asks)),
            sequence=sequence,
            exchange_timestamp=exchange_timestamp,
            received_at=received_at,
        )
        return state.snapshot(depth=self.default_depth_window)

    def apply_level_update(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        *,
        sequence: int | None = None,
        exchange_timestamp: datetime | None = None,
        received_at: datetime | None = None,
    ) -> OrderBookSnapshot:
        state = self._states.setdefault(symbol, _OrderBookState(symbol=symbol))
        state.apply_level_update(
            side,
            price,
            quantity,
            sequence=sequence,
            exchange_timestamp=exchange_timestamp,
            received_at=received_at,
        )
        return state.snapshot(depth=self.default_depth_window)

    def get_snapshot(
        self, symbol: str, *, depth: int | None = None
    ) -> OrderBookSnapshot | None:
        state = self._states.get(symbol)
        if state is None:
            return None
        depth = self.default_depth_window if depth is None else depth
        return state.snapshot(depth=depth)

    def get_best_bid_ask(
        self, symbol: str
    ) -> tuple[OrderBookLevel | None, OrderBookLevel | None]:
        snapshot = self.get_snapshot(symbol, depth=1)
        if snapshot is None:
            return None, None
        return snapshot.best_bid, snapshot.best_ask

    def get_depth_window(
        self, symbol: str, *, depth: int | None = None
    ) -> tuple[tuple[OrderBookLevel, ...], tuple[OrderBookLevel, ...]] | None:
        snapshot = self.get_snapshot(symbol, depth=depth)
        if snapshot is None:
            return None
        return snapshot.bids, snapshot.asks

    def symbols(self) -> tuple[str, ...]:
        return tuple(self._states)

    def clear(self) -> None:
        self._states.clear()

    @staticmethod
    def _coerce_levels(
        levels: Sequence[OrderBookLevel | Mapping[str, float] | tuple[float, float]],
    ) -> Iterable[OrderBookLevel]:
        for level in levels:
            yield OrderBookService._coerce_level(level)

    @staticmethod
    def _coerce_level(
        level: OrderBookLevel | Mapping[str, float] | tuple[float, float],
    ) -> OrderBookLevel:
        if isinstance(level, OrderBookLevel):
            return level
        if isinstance(level, Mapping):
            price = float(level["price"])
            quantity = float(level["quantity"])
            return OrderBookLevel(price=price, quantity=quantity)
        price, quantity = level
        return OrderBookLevel(price=float(price), quantity=float(quantity))
