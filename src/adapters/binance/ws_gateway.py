"""Binance websocket gateway adapter for normalized market-data events."""

from __future__ import annotations

import inspect
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Protocol, cast, runtime_checkable
from uuid import uuid4

from domain.events import (
    BookTickerPayload,
    EventMeta,
    KlinePayload,
    RuntimeEvent,
)
from exchange.websocket_manager import WebSocketManager
from ports.exchange import (
    MarketDataEventHandler,
    MarketDataEventType,
    MarketDataGateway,
)

_SUPPORTED_EVENT_TYPES = {
    "market.tick",
    "market.kline",
    "market.kline_closed",
    "market.spread",
}


@runtime_checkable
class _WebSocketManagerLike(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    def register_callback(self, event_type: str, callback: Any) -> None: ...

    async def subscribe_book_ticker(self, symbol: str) -> None: ...

    async def subscribe_kline(self, symbol: str, interval: str = "1m") -> None: ...


class BinanceWebSocketGateway(MarketDataGateway):
    """Normalize Binance websocket callbacks into runtime events."""

    def __init__(
        self,
        websocket_manager: _WebSocketManagerLike | None = None,
        *,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = True,
    ) -> None:
        if websocket_manager is None:
            websocket_manager = WebSocketManager(
                api_key=api_key,
                api_secret=api_secret,
                testnet=testnet,
            )
        self._manager = websocket_manager
        self._callbacks: dict[str, list[MarketDataEventHandler]] = defaultdict(list)
        self._bridges_registered = False

    async def connect(self) -> None:
        await self._manager.connect()
        self._ensure_bridges()

    async def disconnect(self) -> None:
        await self._manager.disconnect()

    def register_callback(
        self, event_type: MarketDataEventType, callback: MarketDataEventHandler
    ) -> None:
        if event_type not in _SUPPORTED_EVENT_TYPES:
            raise ValueError(f"unsupported market-data event type: {event_type}")
        self._callbacks[event_type].append(callback)

    async def subscribe_book_ticker(self, symbol: str) -> None:
        await self._manager.subscribe_book_ticker(symbol)

    async def subscribe_kline(self, symbol: str, interval: str = "1m") -> None:
        await self._manager.subscribe_kline(symbol, interval=interval)

    def _ensure_bridges(self) -> None:
        if self._bridges_registered:
            return
        self._manager.register_callback("book_ticker", self._handle_book_ticker)
        self._manager.register_callback("kline", self._handle_kline)
        self._bridges_registered = True

    async def _handle_book_ticker(self, data: dict[str, Any]) -> None:
        event = RuntimeEvent(
            meta=EventMeta(
                event_id=uuid4().hex,
                event_type="market.tick",
                source="binance.ws.book_ticker",
                symbol=str(data.get("symbol")) if data.get("symbol") else None,
                ts_exchange=self._coerce_datetime(data.get("timestamp")),
                ts_received=datetime.now(timezone.utc),
            ),
            payload=BookTickerPayload(
                bid_price=float(data.get("bid_price", 0.0)),
                bid_qty=float(data.get("bid_qty", 0.0)),
                ask_price=float(data.get("ask_price", 0.0)),
                ask_qty=float(data.get("ask_qty", 0.0)),
            ),
        )
        await self._dispatch("market.tick", event)

    async def _handle_kline(self, data: dict[str, Any]) -> None:
        event_type = "market.kline_closed" if data.get("is_closed") else "market.kline"
        event = RuntimeEvent(
            meta=EventMeta(
                event_id=uuid4().hex,
                event_type=event_type,
                source=f"binance.ws.kline.{data.get('interval', '1m')}",
                symbol=str(data.get("symbol")) if data.get("symbol") else None,
                ts_exchange=self._coerce_datetime(
                    data.get("timestamp") or data.get("close_time")
                ),
                ts_received=datetime.now(timezone.utc),
            ),
            payload=KlinePayload(
                interval=str(data.get("interval", "1m")),
                open_time=self._coerce_datetime(data.get("open_time"))
                or datetime.now(timezone.utc),
                close_time=self._coerce_datetime(data.get("close_time"))
                or datetime.now(timezone.utc),
                open=float(data.get("open", 0.0)),
                high=float(data.get("high", 0.0)),
                low=float(data.get("low", 0.0)),
                close=float(data.get("close", 0.0)),
                volume=float(data.get("volume", 0.0)),
                is_closed=bool(data.get("is_closed", False)),
            ),
        )
        await self._dispatch(event_type, event)

    async def _dispatch(self, event_type: EventType, event: RuntimeEvent) -> None:
        for callback in self._callbacks[event_type]:
            result = callback(event)
            if inspect.isawaitable(result):
                await cast(Any, result)

    @staticmethod
    def _coerce_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            epoch_value = float(value)
            # Binance sends kline timestamps in milliseconds; treat large
            # numeric epochs as ms and smaller values as seconds.
            if abs(epoch_value) >= 10_000_000_000:
                epoch_value /= 1000.0
            return datetime.fromtimestamp(epoch_value, tz=timezone.utc)
        return None
