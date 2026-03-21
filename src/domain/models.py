"""Live market state models for the event-driven runtime."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True, frozen=True)
class Candle:
    """Single OHLCV candle in live or historical buffers."""

    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class SymbolState:
    """Mutable in-memory state for a single trading symbol."""

    symbol: str
    best_bid: float | None = None
    best_ask: float | None = None
    last_price: float | None = None
    spread_bps: float | None = None
    candles_1m: deque[Candle] = field(default_factory=lambda: deque(maxlen=2048))
    last_kline_close: datetime | None = None

    def update_book_ticker(
        self,
        bid_price: float,
        bid_qty: float,
        ask_price: float,
        ask_qty: float,
        last_price: float | None = None,
    ) -> None:
        """Update the top-of-book snapshot and recompute spread."""

        del bid_qty, ask_qty
        self.best_bid = bid_price
        self.best_ask = ask_price
        if last_price is not None:
            self.last_price = last_price

        if bid_price > 0 and ask_price > 0:
            mid_price = (bid_price + ask_price) / 2
            if mid_price > 0:
                self.spread_bps = ((ask_price - bid_price) / mid_price) * 10000
        else:
            self.spread_bps = None

    def add_candle(self, candle: Candle) -> None:
        """Append a new candle and mark the latest close time."""

        self.candles_1m.append(candle)
        self.last_kline_close = candle.close_time
        self.last_price = candle.close
