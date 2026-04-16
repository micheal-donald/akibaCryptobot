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
        normalized = self._normalize_book_ticker_payload(data)
        event = RuntimeEvent(
            meta=EventMeta(
                event_id=uuid4().hex,
                event_type="market.tick",
                source="binance.ws.book_ticker",
                symbol=normalized["symbol"],
                ts_exchange=self._coerce_datetime(normalized["timestamp"]),
                ts_received=datetime.now(timezone.utc),
            ),
            payload=BookTickerPayload(
                bid_price=float(normalized["bid_price"]),
                bid_qty=float(normalized["bid_qty"]),
                ask_price=float(normalized["ask_price"]),
                ask_qty=float(normalized["ask_qty"]),
            ),
        )
        await self._dispatch("market.tick", event)

    async def _handle_kline(self, data: dict[str, Any]) -> None:
        normalized = self._normalize_kline_payload(data)
        event_type = (
            "market.kline_closed" if normalized["is_closed"] else "market.kline"
        )
        event = RuntimeEvent(
            meta=EventMeta(
                event_id=uuid4().hex,
                event_type=event_type,
                source=f"binance.ws.kline.{normalized['interval']}",
                symbol=normalized["symbol"],
                ts_exchange=self._coerce_datetime(
                    normalized["timestamp"] or normalized["close_time"]
                ),
                ts_received=datetime.now(timezone.utc),
            ),
            payload=KlinePayload(
                interval=str(normalized["interval"]),
                open_time=self._coerce_datetime(normalized["open_time"])
                or datetime.now(timezone.utc),
                close_time=self._coerce_datetime(normalized["close_time"])
                or datetime.now(timezone.utc),
                open=float(normalized["open"]),
                high=float(normalized["high"]),
                low=float(normalized["low"]),
                close=float(normalized["close"]),
                volume=float(normalized["volume"]),
                is_closed=bool(normalized["is_closed"]),
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
        # Mirror user_data_gateway._coerce_datetime so both Binance WS
        # surfaces produce tz-aware UTC datetimes. Previously this path
        # forwarded naive datetimes unchanged, which leaked into
        # EventMeta.ts_exchange / KlinePayload.close_time and eventually
        # crashed latency subtractions in the telemetry service.
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        if isinstance(value, (int, float)):
            epoch_value = float(value)
            # Binance sends kline timestamps in milliseconds; treat large
            # numeric epochs as ms and smaller values as seconds.
            if abs(epoch_value) >= 10_000_000_000:
                epoch_value /= 1000.0
            return datetime.fromtimestamp(epoch_value, tz=timezone.utc)
        return None

    @staticmethod
    def _normalize_book_ticker_payload(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": data.get("symbol") or data.get("s"),
            "bid_price": data.get("bid_price", data.get("b", 0.0)),
            "bid_qty": data.get("bid_qty", data.get("B", 0.0)),
            "ask_price": data.get("ask_price", data.get("a", 0.0)),
            "ask_qty": data.get("ask_qty", data.get("A", 0.0)),
            "timestamp": data.get("timestamp") or data.get("E"),
        }

    @staticmethod
    def _normalize_kline_payload(data: dict[str, Any]) -> dict[str, Any]:
        kline = data.get("k") if isinstance(data.get("k"), dict) else {}
        source = kline if kline else data
        return {
            "symbol": (
                source.get("symbol")
                or data.get("symbol")
                or source.get("s")
                or data.get("s")
            ),
            "interval": (
                source.get("interval")
                or source.get("i")
                or data.get("interval")
                or data.get("i")
                or "1m"
            ),
            "open_time": source.get("open_time", source.get("t")),
            "close_time": source.get("close_time", source.get("T")),
            "open": source.get("open", source.get("o", 0.0)),
            "high": source.get("high", source.get("h", 0.0)),
            "low": source.get("low", source.get("l", 0.0)),
            "close": source.get("close", source.get("c", 0.0)),
            "volume": source.get("volume", source.get("v", 0.0)),
            "is_closed": source.get("is_closed", source.get("x", False)),
            "timestamp": (
                source.get("timestamp")
                or data.get("timestamp")
                or data.get("E")
                or source.get("E")
            ),
        }
