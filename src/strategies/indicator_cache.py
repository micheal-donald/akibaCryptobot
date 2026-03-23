"""Bounded candle windows for runtime strategy evaluation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from domain.models import Candle


@dataclass(slots=True)
class _WindowState:
    candles: deque[Candle] = field(default_factory=deque)
    last_close_time: datetime | None = None
    invalidated_reason: str | None = None


class IndicatorWindowCache:
    """Store bounded per-symbol, per-timeframe candle windows."""

    def __init__(self, *, max_window_size: int = 2048) -> None:
        if max_window_size < 1:
            raise ValueError("max_window_size must be at least 1")
        self.max_window_size = max_window_size
        self._windows: dict[tuple[str, str], _WindowState] = {}

    def append_candle(self, symbol: str, timeframe: str, candle: Candle) -> None:
        """Append a completed candle and reset invalidated windows on gaps."""

        state = self._get_state(symbol, timeframe)
        if state.last_close_time is not None:
            if candle.open_time > state.last_close_time:
                self.invalidate(symbol, timeframe, reason="gap")
                state = self._get_state(symbol, timeframe)
            elif candle.open_time < state.last_close_time:
                self.invalidate(symbol, timeframe, reason="stale")
                state = self._get_state(symbol, timeframe)

        state.candles.append(candle)
        state.last_close_time = candle.close_time
        state.invalidated_reason = None

    def get_window(self, symbol: str, timeframe: str) -> tuple[Candle, ...]:
        """Return the current bounded candle window."""

        state = self._windows.get((symbol, timeframe))
        if state is None:
            return ()
        return tuple(state.candles)

    def get_last_close_time(self, symbol: str, timeframe: str) -> datetime | None:
        """Return the last completed candle close time for a window."""

        state = self._windows.get((symbol, timeframe))
        if state is None:
            return None
        return state.last_close_time

    def get_invalidation_reason(self, symbol: str, timeframe: str) -> str | None:
        """Return the last invalidation reason for a window."""

        state = self._windows.get((symbol, timeframe))
        if state is None:
            return None
        return state.invalidated_reason

    def invalidate(
        self,
        symbol: str,
        timeframe: str | None = None,
        *,
        reason: str = "stale",
    ) -> None:
        """Clear one window or all windows for a symbol."""

        if timeframe is None:
            for key, state in self._windows.items():
                if key[0] == symbol:
                    self._reset_state(state, reason)
            return

        state = self._get_state(symbol, timeframe)
        self._reset_state(state, reason)

    def mark_stale(self, symbol: str, timeframe: str | None = None) -> None:
        """Invalidate a symbol window because market data became stale."""

        self.invalidate(symbol, timeframe, reason="stale")

    def mark_gap(self, symbol: str, timeframe: str | None = None) -> None:
        """Invalidate a symbol window because of a market-data gap."""

        self.invalidate(symbol, timeframe, reason="gap")

    def _get_state(self, symbol: str, timeframe: str) -> _WindowState:
        key = (symbol, timeframe)
        state = self._windows.get(key)
        if state is None:
            state = _WindowState(candles=deque(maxlen=self.max_window_size))
            self._windows[key] = state
        elif state.candles.maxlen != self.max_window_size:
            state.candles = deque(state.candles, maxlen=self.max_window_size)
        return state

    @staticmethod
    def _reset_state(state: _WindowState, reason: str) -> None:
        state.candles.clear()
        state.last_close_time = None
        state.invalidated_reason = reason


__all__ = ["IndicatorWindowCache"]
