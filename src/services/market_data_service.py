"""Market-data service for the phase 1 runtime shell."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field, replace
from typing import cast

from domain.events import BookTickerPayload, KlinePayload, RuntimeEvent
from domain.models import Candle, SymbolState
from ports.exchange import MarketDataGateway
from runtime.config import RuntimeConfig

from .base import BaseService


@dataclass(slots=True)
class MarketDataService(BaseService):
    """Owns live symbol state and forwards normalized market events."""

    gateway: MarketDataGateway
    runtime_config: RuntimeConfig
    event_bus: asyncio.Queue[RuntimeEvent]
    _symbol_states: dict[str, SymbolState] = field(default_factory=dict, init=False)
    _exchange_to_canonical: dict[str, str] = field(
        default_factory=dict, init=False, repr=False
    )
    _candle_buffers: dict[tuple[str, str], deque[Candle]] = field(
        default_factory=dict, init=False, repr=False
    )
    _callbacks_registered: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        BaseService.__post_init__(self)
        if not self.runtime_config.symbols:
            self.logger.warning("MarketDataService configured with no symbols")

    async def on_start(self) -> None:
        self._reset_runtime_state()
        self._register_gateway_callbacks()

        try:
            await self.gateway.connect()
            await self._subscribe_configured_symbols()
        except Exception:
            await self.gateway.disconnect()
            raise

    async def on_stop(self) -> None:
        await self.gateway.disconnect()

    def get_symbol_state(self, symbol: str) -> SymbolState | None:
        """Return a read-only snapshot of the current state for a symbol."""

        state = self._symbol_states.get(symbol)
        if state is None:
            return None
        return self._copy_symbol_state(state)

    def get_all_symbol_states(self) -> tuple[SymbolState, ...]:
        """Return snapshots of all tracked symbols."""

        return tuple(
            self._copy_symbol_state(state) for state in self._symbol_states.values()
        )

    def get_candles(self, symbol: str, interval: str) -> tuple[Candle, ...]:
        """Return a snapshot of buffered candles for a symbol/interval."""

        key = (symbol, interval)
        buffer = self._candle_buffers.get(key)
        if buffer is None:
            return ()
        return tuple(buffer)

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

    def _register_gateway_callbacks(self) -> None:
        if self._callbacks_registered:
            return
        self.gateway.register_callback("market.tick", self._handle_market_event)
        self.gateway.register_callback("market.kline", self._handle_market_event)
        self.gateway.register_callback("market.kline_closed", self._handle_market_event)
        self._callbacks_registered = True

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
        self._candle_buffers.clear()

    def _create_symbol_state(self, symbol: str) -> SymbolState:
        state = SymbolState(symbol=symbol)
        state.candles_1m = deque(maxlen=self.runtime_config.candle_history_limit)
        return state

    def _resolve_canonical_symbol(self, symbol: str | None) -> str | None:
        if symbol is None:
            return None
        return self._exchange_to_canonical.get(symbol, symbol)

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

    def _update_kline(self, symbol: str, event: RuntimeEvent) -> None:
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

        buffer = self._candle_buffers.get((symbol, payload.interval))
        if buffer is None:
            buffer = deque(maxlen=self.runtime_config.candle_history_limit)
            self._candle_buffers[(symbol, payload.interval)] = buffer
        buffer.append(candle)

        state = self._symbol_states.setdefault(
            symbol, self._create_symbol_state(symbol)
        )
        if payload.interval == self.runtime_config.kline_intervals[0]:
            state.add_candle(candle)
        else:
            state.last_kline_close = candle.close_time
            state.last_price = candle.close

    @staticmethod
    def _to_exchange_symbol(symbol: str) -> str:
        return symbol.replace("/", "").replace("-", "").replace(" ", "")

    @staticmethod
    def _copy_symbol_state(state: SymbolState) -> SymbolState:
        copied = replace(state)
        copied.candles_1m = deque(state.candles_1m, maxlen=state.candles_1m.maxlen)
        return copied
