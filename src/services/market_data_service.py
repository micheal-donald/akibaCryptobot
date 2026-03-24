"""Market-data service for the phase 2 runtime shell."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import cast

from adapters.binance.symbol_mapper import BinanceSymbolMapper
from domain.events import BookTickerPayload, KlinePayload, RuntimeEvent
from domain.models import Candle, SymbolState
from ports.exchange import MarketDataBootstrapGateway, MarketDataGateway
from runtime.config import RuntimeConfig

from .base import BaseService


@dataclass(slots=True)
class MarketDataService(BaseService):
    """Owns live symbol state and forwards normalized market events."""

    gateway: MarketDataGateway
    runtime_config: RuntimeConfig
    event_bus: asyncio.Queue[RuntimeEvent]
    bootstrap_gateway: MarketDataBootstrapGateway | None = None
    event_sinks: tuple[asyncio.Queue[RuntimeEvent], ...] = ()
    _symbol_states: dict[str, SymbolState] = field(default_factory=dict, init=False)
    _exchange_to_canonical: dict[str, str] = field(
        default_factory=dict, init=False, repr=False
    )
    _callbacks_registered: bool = field(default=False, init=False, repr=False)
    _symbol_mapper: BinanceSymbolMapper = field(
        default_factory=BinanceSymbolMapper, init=False, repr=False
    )

    def __post_init__(self) -> None:
        BaseService.__post_init__(self)
        if not self.runtime_config.symbols:
            self.logger.warning("MarketDataService configured with no symbols")

    async def on_start(self) -> None:
        self._reset_runtime_state()
        self._register_gateway_callbacks()

        try:
            await self._bootstrap_configured_symbols()
            await self.gateway.connect()
            await self._subscribe_configured_symbols()
        except Exception:
            await self.gateway.disconnect()
            raise

    async def on_stop(self) -> None:
        await self.gateway.disconnect()

    def get_symbol_state(self, symbol: str) -> SymbolState | None:
        """Return a read-only snapshot of the current state for a symbol."""

        self.refresh_stale_state()
        state = self._symbol_states.get(symbol)
        if state is None:
            return None
        return self._copy_symbol_state(state)

    def get_all_symbol_states(self) -> tuple[SymbolState, ...]:
        """Return snapshots of all tracked symbols."""

        self.refresh_stale_state()
        return tuple(
            self._copy_symbol_state(state) for state in self._symbol_states.values()
        )

    def get_candles(self, symbol: str, interval: str) -> tuple[Candle, ...]:
        """Return a snapshot of buffered candles for a symbol/interval."""

        self.refresh_stale_state()
        state = self._symbol_states.get(symbol)
        if state is None:
            return ()
        buffer = state.candle_buffers.get(interval)
        if buffer is None:
            return ()
        return tuple(buffer)

    def refresh_stale_state(self, *, now: datetime | None = None) -> tuple[str, ...]:
        """Recompute stale markers from the latest known timestamps."""

        current_time = now or datetime.now(timezone.utc)
        stale_symbols: list[str] = []

        for symbol, state in self._symbol_states.items():
            if self._is_state_stale(state, current_time):
                if not state.is_stale:
                    state.mark_stale(current_time)
                stale_symbols.append(symbol)
            elif state.is_stale:
                state.is_stale = False
                state.stale_since = None

        return tuple(stale_symbols)

    async def _handle_market_event(self, event: RuntimeEvent) -> None:
        symbol = self._resolve_canonical_symbol(event.meta.symbol)
        if symbol is None:
            self.logger.debug("Dropping market event with no symbol: %s", event.meta)
            return

        if event.meta.event_type == "market.tick":
            self._update_book_ticker(symbol, event)
        elif event.meta.event_type in {"market.kline", "market.kline_closed"}:
            self._update_kline(symbol, event)

        await self.event_bus.put(event)
        for sink in self.event_sinks:
            if sink is self.event_bus:
                continue
            await sink.put(event)

    def _register_gateway_callbacks(self) -> None:
        if self._callbacks_registered:
            return
        self.gateway.register_callback("market.tick", self._handle_market_event)
        self.gateway.register_callback("market.kline", self._handle_market_event)
        self.gateway.register_callback("market.kline_closed", self._handle_market_event)
        self._callbacks_registered = True

    async def _bootstrap_configured_symbols(self) -> None:
        for symbol in self.runtime_config.symbols:
            state = self._symbol_states.setdefault(
                symbol, self._create_symbol_state(symbol)
            )
            exchange_symbol = self._to_exchange_symbol(symbol)
            self._exchange_to_canonical[exchange_symbol] = symbol

            if self.bootstrap_gateway is None:
                continue

            for interval in self.runtime_config.kline_intervals:
                try:
                    candles = await self.bootstrap_gateway.fetch_ohlcv(
                        exchange_symbol,
                        interval,
                        self.runtime_config.candle_history_limit,
                    )
                except Exception as exc:
                    self.logger.warning(
                        "Bootstrap failed for %s %s: %s", symbol, interval, exc
                    )
                    state.mark_stale()
                    continue

                if not candles:
                    continue

                buffer = state.ensure_candle_buffer(
                    interval, maxlen=self.runtime_config.candle_history_limit
                )
                buffer.clear()
                for candle in candles:
                    buffer.append(candle)
                if interval == "1m":
                    state.candles_1m = buffer
                state.last_kline_by_interval[interval] = candles[-1].close_time
                state.last_kline_close = candles[-1].close_time
                state.last_price = candles[-1].close
                state.last_update_at = candles[-1].close_time
                state.is_stale = False
                state.stale_since = None

    async def _subscribe_configured_symbols(self) -> None:
        for symbol in self.runtime_config.symbols:
            exchange_symbol = self._to_exchange_symbol(symbol)
            self._exchange_to_canonical[exchange_symbol] = symbol
            self._symbol_states.setdefault(symbol, self._create_symbol_state(symbol))
            await self.gateway.subscribe_book_ticker(exchange_symbol)
            for interval in self.runtime_config.kline_intervals:
                await self.gateway.subscribe_kline(exchange_symbol, interval)

    def _reset_runtime_state(self) -> None:
        self._symbol_states.clear()
        self._exchange_to_canonical.clear()

    def _create_symbol_state(self, symbol: str) -> SymbolState:
        state = SymbolState(symbol=symbol)
        state.candles_1m = deque(maxlen=self.runtime_config.candle_history_limit)
        state.candle_buffers["1m"] = state.candles_1m
        return state

    def _resolve_canonical_symbol(self, symbol: str | None) -> str | None:
        if symbol is None:
            return None
        resolved = self._exchange_to_canonical.get(symbol)
        if resolved is not None:
            return resolved
        try:
            return self._symbol_mapper.to_canonical_symbol(symbol)
        except ValueError:
            return symbol

    def _update_book_ticker(self, symbol: str, event: RuntimeEvent) -> None:
        state = self._symbol_states.setdefault(
            symbol, self._create_symbol_state(symbol)
        )
        payload = cast(BookTickerPayload, event.payload)
        bid_price = float(payload.bid_price)
        bid_qty = float(payload.bid_qty)
        ask_price = float(payload.ask_price)
        ask_qty = float(payload.ask_qty)
        mid_price = (
            (bid_price + ask_price) / 2 if bid_price > 0 and ask_price > 0 else None
        )
        state.update_book_ticker(
            bid_price=bid_price,
            bid_qty=bid_qty,
            ask_price=ask_price,
            ask_qty=ask_qty,
            last_price=mid_price,
        )
        state.last_update_at = event.meta.ts_received
        state.last_book_ticker_at = event.meta.ts_received
        state.is_stale = False
        state.stale_since = None

    def _update_kline(self, symbol: str, event: RuntimeEvent) -> None:
        state = self._symbol_states.setdefault(
            symbol, self._create_symbol_state(symbol)
        )
        payload = cast(KlinePayload, event.payload)
        candle = Candle(
            open_time=payload.open_time,
            close_time=payload.close_time,
            open=payload.open,
            high=payload.high,
            low=payload.low,
            close=payload.close,
            volume=payload.volume,
        )

        buffer = state.ensure_candle_buffer(
            payload.interval, maxlen=self.runtime_config.candle_history_limit
        )
        buffer.append(candle)
        if payload.interval == "1m":
            state.candles_1m = buffer
        state.last_kline_by_interval[payload.interval] = candle.close_time
        state.last_kline_close = candle.close_time
        state.last_price = candle.close
        state.last_update_at = event.meta.ts_received
        state.is_stale = False
        state.stale_since = None

    def _is_state_stale(self, state: SymbolState, now: datetime) -> bool:
        if state.last_book_ticker_at is not None:
            book_ticker_age = now - state.last_book_ticker_at
            if book_ticker_age > timedelta(
                seconds=self.runtime_config.stale_after_seconds
            ):
                return True
        elif state.last_update_at is None:
            return True
        elif now - state.last_update_at > timedelta(
            seconds=self.runtime_config.stale_after_seconds
        ):
            return True

        if not self.runtime_config.kline_intervals:
            return False

        for interval in self.runtime_config.kline_intervals:
            last_kline = state.last_kline_by_interval.get(interval)
            if last_kline is None:
                return True
            max_age = timedelta(
                seconds=self._interval_seconds(interval)
                + self._interval_tolerance_seconds(interval)
            )
            if now - last_kline > max_age:
                return True

        return False

    def _interval_seconds(self, interval: str) -> float:
        interval = interval.strip().lower()
        if not interval:
            return self.runtime_config.stale_after_seconds

        suffix = interval[-1]
        try:
            value = float(interval[:-1])
        except ValueError:
            return self.runtime_config.stale_after_seconds

        if suffix == "m":
            return value * 60.0
        if suffix == "h":
            return value * 3600.0
        if suffix == "d":
            return value * 86400.0
        if suffix == "w":
            return value * 604800.0
        return self.runtime_config.stale_after_seconds

    def _interval_tolerance_seconds(self, interval: str) -> float:
        base_seconds = self._interval_seconds(interval)
        return max(5.0, base_seconds * 0.1)

    def _to_exchange_symbol(self, symbol: str) -> str:
        return self._symbol_mapper.to_exchange_symbol(symbol)

    @staticmethod
    def _copy_symbol_state(state: SymbolState) -> SymbolState:
        copied = SymbolState(
            symbol=state.symbol,
            best_bid=state.best_bid,
            best_ask=state.best_ask,
            last_price=state.last_price,
            spread_bps=state.spread_bps,
            candles_1m=deque(state.candles_1m, maxlen=state.candles_1m.maxlen),
            candle_buffers={},
            last_update_at=state.last_update_at,
            last_book_ticker_at=state.last_book_ticker_at,
            last_kline_by_interval=dict(state.last_kline_by_interval),
            last_kline_close=state.last_kline_close,
            is_stale=state.is_stale,
            stale_since=state.stale_since,
        )

        for interval, buffer in state.candle_buffers.items():
            copied_buffer = deque(buffer, maxlen=buffer.maxlen)
            copied.candle_buffers[interval] = copied_buffer
            if interval == "1m":
                copied.candles_1m = copied_buffer

        if "1m" not in copied.candle_buffers:
            copied.candle_buffers["1m"] = copied.candles_1m

        return copied
