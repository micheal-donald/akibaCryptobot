"""Binance user-data gateway adapter for normalized execution events."""

from __future__ import annotations

import inspect
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Protocol, cast, runtime_checkable
from uuid import uuid4

from adapters.binance.symbol_mapper import BinanceSymbolMapper
from domain.events import (
    BalanceUpdatePayload,
    EventMeta,
    FillPayload,
    OrderUpdatePayload,
    RuntimeEvent,
)
from exchange.websocket_manager import WebSocketManager
from ports.exchange import (
    UserStreamEventHandler,
    UserStreamEventType,
    UserStreamGateway,
)

_SUPPORTED_EVENT_TYPES = {
    "execution.order_update",
    "execution.fill",
    "execution.balance_update",
}
_SYMBOL_MAPPER = BinanceSymbolMapper()


@runtime_checkable
class _WebSocketManagerLike(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    def register_callback(self, event_type: str, callback: Any) -> None: ...

    async def subscribe_user_data(self) -> None: ...


class BinanceUserDataGateway(UserStreamGateway):
    """Normalize Binance user-data callbacks into runtime execution events."""

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
        self._callbacks: dict[str, list[UserStreamEventHandler]] = defaultdict(list)
        self._bridge_registered = False

    async def connect(self) -> None:
        await self._manager.connect()
        self._ensure_bridge()

    async def disconnect(self) -> None:
        await self._manager.disconnect()

    async def subscribe_user_stream(self) -> None:
        self._ensure_bridge()
        await self._manager.subscribe_user_data()

    def register_order_update_handler(self, handler: UserStreamEventHandler) -> None:
        self._callbacks["execution.order_update"].append(handler)

    def register_fill_handler(self, handler: UserStreamEventHandler) -> None:
        self._callbacks["execution.fill"].append(handler)

    def register_balance_update_handler(self, handler: UserStreamEventHandler) -> None:
        self._callbacks["execution.balance_update"].append(handler)

    def _ensure_bridge(self) -> None:
        if self._bridge_registered:
            return
        self._manager.register_callback("user", self._handle_user_event)
        self._bridge_registered = True

    async def _handle_user_event(self, data: dict[str, Any]) -> None:
        event_type = self._detect_event_type(data)
        if event_type is None:
            return

        if event_type == "execution.balance_update":
            event = self._build_balance_update_event(data)
            if event is not None:
                await self._dispatch(event_type, event)
            return

        if event_type == "execution.order_update":
            order_event = self._build_order_update_event(data)
            if order_event is not None:
                await self._dispatch(event_type, order_event)

            fill_event = self._build_fill_event(data)
            if fill_event is not None:
                await self._dispatch("execution.fill", fill_event)

    async def _dispatch(
        self, event_type: UserStreamEventType, event: RuntimeEvent
    ) -> None:
        for callback in self._callbacks[event_type]:
            result = callback(event)
            if inspect.isawaitable(result):
                await cast(Any, result)

    def _build_order_update_event(self, data: dict[str, Any]) -> RuntimeEvent | None:
        symbol = self._normalize_symbol(
            data.get("symbol") or data.get("s") or data.get("symbol_name")
        )
        if symbol is None:
            return None

        payload = OrderUpdatePayload(
            client_order_id=self._string_or_none(
                data.get("client_order_id")
                or data.get("clientOrderId")
                or data.get("c")
            ),
            exchange_order_id=self._string_or_none(
                data.get("order_id") or data.get("orderId") or data.get("i")
            ),
            symbol=symbol,
            status=self._normalize_status(data),
            filled_qty=self._float_or_default(
                data.get("filled_quantity") or data.get("filledQty") or data.get("z"),
                default=0.0,
            ),
            remaining_qty=self._float_or_default(
                data.get("remaining_quantity")
                or data.get("remainingQty")
                or self._derive_remaining_qty(data),
                default=0.0,
            ),
            avg_fill_price=self._optional_float(
                data.get("avg_fill_price")
                or data.get("avgPrice")
                or data.get("average")
                or data.get("L")
            ),
            reduce_only=self._bool_or_default(
                data.get("reduce_only") or data.get("reduceOnly"),
                default=False,
            ),
            raw_status=self._string_or_none(data.get("raw_status") or data.get("X")),
        )

        return RuntimeEvent(
            meta=EventMeta(
                event_id=uuid4().hex,
                event_type="execution.order_update",
                source="binance.ws.user_data",
                symbol=symbol,
                ts_exchange=self._coerce_datetime(
                    data.get("timestamp") or data.get("E")
                ),
                ts_received=datetime.now(timezone.utc),
            ),
            payload=payload,
        )

    def _build_fill_event(self, data: dict[str, Any]) -> RuntimeEvent | None:
        symbol = self._normalize_symbol(
            data.get("symbol") or data.get("s") or data.get("symbol_name")
        )
        if symbol is None:
            return None

        fill_qty = self._float_or_default(
            data.get("last_fill_qty")
            or data.get("lastExecutedQty")
            or data.get("last_executed_qty")
            or data.get("l")
            or data.get("filled_quantity")
            or data.get("filledQty")
            or data.get("z"),
            default=0.0,
        )
        if fill_qty <= 0:
            return None

        payload = FillPayload(
            client_order_id=self._string_or_none(
                data.get("client_order_id")
                or data.get("clientOrderId")
                or data.get("c")
            ),
            exchange_order_id=self._string_or_none(
                data.get("order_id") or data.get("orderId") or data.get("i")
            ),
            symbol=symbol,
            side=self._normalize_side(data.get("side") or data.get("S")),
            fill_qty=fill_qty,
            fill_price=self._float_or_default(
                data.get("fill_price")
                or data.get("lastPrice")
                or data.get("L")
                or data.get("price")
                or 0.0,
                default=0.0,
            ),
            cumulative_qty=self._float_or_default(
                data.get("cumulative_qty")
                or data.get("filled_quantity")
                or data.get("z"),
                default=fill_qty,
            ),
            cumulative_quote_qty=self._optional_float(
                data.get("cumulative_quote_qty")
                or data.get("quoteQty")
                or data.get("Z")
            ),
            fee_cost=self._optional_float(data.get("fee_cost") or data.get("n")),
            fee_currency=self._string_or_none(
                data.get("fee_currency") or data.get("N")
            ),
            is_maker=self._optional_bool(data.get("is_maker") or data.get("m")),
            reduce_only=self._bool_or_default(
                data.get("reduce_only") or data.get("reduceOnly"),
                default=False,
            ),
        )

        return RuntimeEvent(
            meta=EventMeta(
                event_id=uuid4().hex,
                event_type="execution.fill",
                source="binance.ws.user_data",
                symbol=symbol,
                ts_exchange=self._coerce_datetime(
                    data.get("timestamp") or data.get("E")
                ),
                ts_received=datetime.now(timezone.utc),
            ),
            payload=payload,
        )

    def _build_balance_update_event(self, data: dict[str, Any]) -> RuntimeEvent | None:
        balances = data.get("balances") or data.get("B")
        normalized_balances = self._normalize_balances(balances)
        if not normalized_balances:
            return None

        return RuntimeEvent(
            meta=EventMeta(
                event_id=uuid4().hex,
                event_type="execution.balance_update",
                source="binance.ws.user_data",
                symbol=None,
                ts_exchange=self._coerce_datetime(
                    data.get("timestamp") or data.get("E")
                ),
                ts_received=datetime.now(timezone.utc),
            ),
            payload=BalanceUpdatePayload(balances=normalized_balances),
        )

    @staticmethod
    def _derive_remaining_qty(data: dict[str, Any]) -> float | None:
        original_qty = BinanceUserDataGateway._optional_float(
            data.get("original_quantity") or data.get("originalQty") or data.get("q")
        )
        filled_qty = BinanceUserDataGateway._optional_float(
            data.get("filled_quantity") or data.get("filledQty") or data.get("z")
        )
        if original_qty is None:
            return None
        return max(original_qty - (filled_qty or 0.0), 0.0)

    @staticmethod
    def _detect_event_type(data: dict[str, Any]) -> str | None:
        event_type = data.get("type") or data.get("e")
        if event_type in {"order_update", "executionReport"}:
            return "execution.order_update"
        if event_type in {"balance_update", "outboundAccountPosition", "balanceUpdate"}:
            return "execution.balance_update"
        if event_type in {"balance_update"}:
            return "execution.balance_update"
        return None

    @staticmethod
    def _normalize_symbol(symbol: Any) -> str | None:
        if symbol is None:
            return None
        text = str(symbol).strip()
        if not text:
            return None
        try:
            return _SYMBOL_MAPPER.to_canonical_symbol(text)
        except ValueError:
            return None

    @staticmethod
    def _normalize_side(side: Any) -> str:
        text = str(side or "").strip().lower()
        if text in {"buy", "b"}:
            return "buy"
        if text in {"sell", "s"}:
            return "sell"
        return "buy"

    @staticmethod
    def _normalize_status(data: dict[str, Any]) -> str:
        status = str(
            data.get("status") or data.get("X") or data.get("raw_status") or "unknown"
        )
        normalized = status.strip().lower()
        mapping = {
            "new": "accepted",
            "partially_filled": "partially_filled",
            "partiallyfilled": "partially_filled",
            "filled": "filled",
            "canceled": "cancelled",
            "cancelled": "cancelled",
            "rejected": "rejected",
            "expired": "expired",
        }
        return mapping.get(normalized, normalized)

    @staticmethod
    def _normalize_balances(balances: Any) -> dict[str, dict[str, float]]:
        if not isinstance(balances, (list, tuple)):
            return {}

        normalized: dict[str, dict[str, float]] = {}
        for item in balances:
            if not isinstance(item, dict):
                continue
            asset = str(item.get("a") or item.get("asset") or "").strip().upper()
            if not asset:
                continue
            normalized[asset] = {
                "free": BinanceUserDataGateway._float_or_default(
                    item.get("f") or item.get("free"),
                    default=0.0,
                ),
                "locked": BinanceUserDataGateway._float_or_default(
                    item.get("l") or item.get("locked"),
                    default=0.0,
                ),
            }
        return normalized

    @staticmethod
    def _coerce_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        if isinstance(value, (int, float)):
            epoch_value = float(value)
            if abs(epoch_value) >= 10_000_000_000:
                epoch_value /= 1000.0
            return datetime.fromtimestamp(epoch_value, tz=timezone.utc)
        return None

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float_or_default(value: Any, *, default: float) -> float:
        if value is None or value == "":
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _bool_or_default(value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y"}:
                return True
            if normalized in {"false", "0", "no", "n"}:
                return False
        return bool(value)

    @staticmethod
    def _optional_bool(value: Any) -> bool | None:
        if value is None:
            return None
        return BinanceUserDataGateway._bool_or_default(value, default=False)
