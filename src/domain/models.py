"""Live market state models for the event-driven runtime."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone


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
    candle_buffers: dict[str, deque[Candle]] = field(default_factory=dict)
    last_update_at: datetime | None = None
    last_book_ticker_at: datetime | None = None
    last_kline_by_interval: dict[str, datetime] = field(default_factory=dict)
    last_kline_close: datetime | None = None
    is_stale: bool = False
    stale_since: datetime | None = None

    def ensure_candle_buffer(
        self, interval: str, *, maxlen: int = 2048
    ) -> deque[Candle]:
        """Return the rolling buffer for an interval, creating it if needed."""

        buffer = self.candle_buffers.get(interval)
        if buffer is not None:
            return buffer

        if interval == "1m":
            buffer = self.candles_1m
            if buffer.maxlen != maxlen:
                buffer = deque(buffer, maxlen=maxlen)
                self.candles_1m = buffer
        else:
            buffer = deque(maxlen=maxlen)

        self.candle_buffers[interval] = buffer
        if interval == "1m":
            self.candles_1m = buffer
        return buffer

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
        self.last_book_ticker_at = datetime.now(timezone.utc)
        self.last_update_at = self.last_book_ticker_at
        self.is_stale = False
        self.stale_since = None

        if bid_price > 0 and ask_price > 0:
            mid_price = (bid_price + ask_price) / 2
            if mid_price > 0:
                self.spread_bps = ((ask_price - bid_price) / mid_price) * 10000
        else:
            self.spread_bps = None

    def add_candle(self, candle: Candle, interval: str = "1m") -> None:
        """Append a new candle and mark the latest close time."""

        buffer = self.ensure_candle_buffer(interval)
        buffer.append(candle)
        if interval == "1m":
            self.candles_1m = buffer
        self.last_kline_by_interval[interval] = candle.close_time
        self.last_kline_close = candle.close_time
        self.last_price = candle.close
        self.last_update_at = candle.close_time
        self.is_stale = False
        self.stale_since = None

    def mark_stale(self, stale_since: datetime | None = None) -> None:
        """Mark the symbol as stale without clearing its last good state."""

        self.is_stale = True
        self.stale_since = stale_since or self.last_update_at
