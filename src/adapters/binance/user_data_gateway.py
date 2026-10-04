"""Binance websocket user-data adapter for normalized execution events."""

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
from ports.exchange import UserStreamEventHandler, UserStreamGateway

_SYMBOL_MAPPER = BinanceSymbolMapper()
_SUPPORTED_EVENT_TYPES = {
    "execution.order_update",
    "execution.fill",
    "execution.balance_update",
}
_STATUS_MAP = {
    "NEW": "accepted",
    "OPEN": "accepted",
    "PARTIALLY_FILLED": "partially_filled",
    "FILLED": "filled",
    "CANCELED": "cancelled",
    "CANCELLED": "cancelled",
    "PENDING_CANCEL": "cancel_pending",
    "REJECTED": "rejected",
    "EXPIRED": "expired",
}


@runtime_checkable
class _WebSocketManagerLike(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    def register_callback(self, event_type: str, callback: Any) -> None: ...

    async def subscribe_user_data(self) -> None: ...


class BinanceUserDataGateway(UserStreamGateway):
    """Normalize Binance user-data callbacks into typed runtime execution events."""

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
        self._bridges_registered = False

    async def connect(self) -> None:
        await self._manager.connect()
        self._ensure_bridges()

    async def disconnect(self) -> None:
        await self._manager.disconnect()

    async def subscribe_user_stream(self) -> None:
        self._ensure_bridges()
        await self._manager.subscribe_user_data()

    def get_status(self) -> dict[str, Any]:
        stream = getattr(self._manager, "spot_user_stream", None)
        if stream is None:
            return {"connected": None, "transport": "unknown"}
        return stream.get_status()

    def register_order_update_handler(self, handler: UserStreamEventHandler) -> None:
        self._callbacks["execution.order_update"].append(handler)

    def register_fill_handler(self, handler: UserStreamEventHandler) -> None:
        self._callbacks["execution.fill"].append(handler)

    def register_balance_update_handler(self, handler: UserStreamEventHandler) -> None:
        self._callbacks["execution.balance_update"].append(handler)

    def _ensure_bridges(self) -> None:
        if self._bridges_registered:
            return
        self._manager.register_callback("user", self._handle_user_event)
        self._bridges_registered = True

    async def _handle_user_event(self, data: dict[str, Any]) -> None:
        try:
            order_update_event = self._normalize_order_update_event(data)
            if order_update_event is not None:
                await self._dispatch("execution.order_update", order_update_event)

            fill_event = self._normalize_fill_event(data)
            if fill_event is not None:
                await self._dispatch("execution.fill", fill_event)

            balance_event = self._normalize_balance_update_event(data)
            if balance_event is not None:
                await self._dispatch("execution.balance_update", balance_event)
        except (TypeError, ValueError):
            return

    async def _dispatch(self, event_type: str, event: RuntimeEvent) -> None:
        if event_type not in _SUPPORTED_EVENT_TYPES:
            raise ValueError(f"unsupported user-stream event type: {event_type}")
        for callback in self._callbacks[event_type]:
            result = callback(event)
            if inspect.isawaitable(result):
                await cast(Any, result)

    def _normalize_order_update_event(
        self, data: dict[str, Any]
    ) -> RuntimeEvent | None:
        event_type = data.get("type")
        if event_type != "order_update" and data.get("e") != "executionReport":
            return None

        raw_status = self._extract_value(data, "status", "X")
        symbol = self._normalize_symbol(self._extract_value(data, "symbol", "s"))
        if symbol is None:
            return None
        filled_qty = self._extract_float(data, "filled_quantity", "z", default=0.0)
        requested_qty = self._extract_float(data, "quantity", "q", default=0.0)
        remaining_qty = self._extract_float(
            data,
            "remaining_quantity",
            "remainingQty",
            default=max(requested_qty - filled_qty, 0.0),
        )
        cumulative_quote = self._extract_optional_float(
            data, "cumulative_quote_quantity", "Z"
        )
        avg_fill_price = self._extract_average_fill_price(
            data,
            filled_qty=filled_qty,
            cumulative_quote=cumulative_quote,
        )

        return RuntimeEvent(
            meta=EventMeta(
                event_id=uuid4().hex,
                event_type="execution.order_update",
                source="binance.ws.user.execution_report",
                symbol=symbol,
                ts_exchange=self._extract_event_timestamp(data),
                ts_received=datetime.now(timezone.utc),
            ),
            payload=OrderUpdatePayload(
                client_order_id=self._extract_string(data, "client_order_id", "c"),
                exchange_order_id=self._extract_string(data, "order_id", "i"),
                symbol=symbol,
                status=self._normalize_status(raw_status),
                filled_qty=filled_qty,
                remaining_qty=remaining_qty,
                avg_fill_price=avg_fill_price,
                reduce_only=bool(
                    self._extract_value(data, "reduce_only", "R") or False
                ),
                raw_status=str(raw_status) if raw_status is not None else None,
            ),
        )

    def _normalize_fill_event(self, data: dict[str, Any]) -> RuntimeEvent | None:
        event_type = data.get("type")
        execution_type = self._extract_string(data, "execution_type", "x")
        last_fill_qty = self._extract_float(
            data,
            "last_filled_quantity",
            "last_fill_qty",
            default=0.0,
        )
        if last_fill_qty <= 0:
            last_fill_qty = self._extract_float(
                data,
                "l",
                "lastExecutedQty",
                default=0.0,
            )
        if last_fill_qty <= 0:
            last_fill_qty = self._extract_float(
                data,
                "last_executed_qty",
                "filled_quantity",
                default=0.0,
            )
        if data.get("e") == "executionReport":
            if execution_type != "TRADE" or last_fill_qty <= 0:
                return None
        elif event_type == "order_update":
            if execution_type not in {"TRADE", "trade"} or last_fill_qty <= 0:
                return None
        else:
            return None

        symbol = self._normalize_symbol(self._extract_value(data, "symbol", "s"))
        side = self._normalize_side(self._extract_value(data, "side", "S"))
        if symbol is None or side is None:
            return None
        fill_price = self._extract_optional_float(data, "last_fill_price", "L")
        if fill_price is None:
            fill_price = self._extract_optional_float(data, "fill_price", "price")
        if fill_price is None:
            fill_price = self._extract_optional_float(data, "lastPrice", "p")
        cumulative_qty = self._extract_float(
            data,
            "filled_quantity",
            "z",
            default=last_fill_qty,
        )

        return RuntimeEvent(
            meta=EventMeta(
                event_id=uuid4().hex,
                event_type="execution.fill",
                source="binance.ws.user.execution_report",
                symbol=symbol,
                ts_exchange=self._extract_event_timestamp(data),
                ts_received=datetime.now(timezone.utc),
            ),
            payload=FillPayload(
                client_order_id=self._extract_string(data, "client_order_id", "c"),
                exchange_order_id=self._extract_string(data, "order_id", "i"),
                symbol=symbol,
                side=side,
                fill_qty=last_fill_qty,
                fill_price=float(fill_price or 0.0),
                cumulative_qty=cumulative_qty,
                cumulative_quote_qty=self._extract_optional_float(
                    data, "cumulative_quote_quantity", "Z"
                ),
                fee_cost=self._extract_optional_float(data, "fee_cost", "n"),
                fee_currency=self._extract_string(data, "fee_currency", "N"),
                is_maker=self._extract_optional_bool(data, "is_maker", "m"),
                reduce_only=bool(
                    self._extract_value(data, "reduce_only", "R") or False
                ),
            ),
        )

    def _normalize_balance_update_event(
        self, data: dict[str, Any]
    ) -> RuntimeEvent | None:
        event_type = data.get("type")
        if (
            event_type != "balance_update"
            and data.get("e") != "outboundAccountPosition"
        ):
            return None

        balances = self._normalize_balances(data.get("balances") or data.get("B"))
        if not balances:
            return None

        return RuntimeEvent(
            meta=EventMeta(
                event_id=uuid4().hex,
                event_type="execution.balance_update",
                source="binance.ws.user.account_position",
                symbol=None,
                ts_exchange=self._extract_event_timestamp(data),
                ts_received=datetime.now(timezone.utc),
            ),
            payload=BalanceUpdatePayload(balances=balances),
        )

    def _extract_average_fill_price(
        self,
        data: dict[str, Any],
        *,
        filled_qty: float,
        cumulative_quote: float | None,
    ) -> float | None:
        direct_value = self._extract_optional_float(data, "avg_fill_price", "avgPrice")
        if direct_value is not None:
            return direct_value
        direct_value = self._extract_optional_float(data, "last_fill_price", "L")
        if direct_value is not None:
            return direct_value
        if cumulative_quote is not None and filled_qty > 0:
            return cumulative_quote / filled_qty
        return None

    def _extract_event_timestamp(self, data: dict[str, Any]) -> datetime | None:
        for key in (
            "trade_time",
            "T",
            "event_time",
            "E",
            "update_time",
            "u",
            "timestamp",
        ):
            value = data.get(key)
            coerced = self._coerce_datetime(value)
            if coerced is not None:
                return coerced
        return None

    @staticmethod
    def _normalize_symbol(symbol: Any) -> str | None:
        if symbol is None:
            return None
        text = str(symbol).strip()
        if not text:
            return None
        if "/" in text:
            return _SYMBOL_MAPPER.to_canonical_symbol(
                _SYMBOL_MAPPER.to_exchange_symbol(text)
            )
        return _SYMBOL_MAPPER.to_canonical_symbol(text)

    @staticmethod
    def _normalize_side(side: Any) -> str | None:
        normalized = str(side or "").strip().lower()
        if normalized not in {"buy", "sell"}:
            return None
        return normalized

    @staticmethod
    def _normalize_status(status: Any) -> str:
        normalized = str(status).strip().upper()
        if not normalized:
            return "unknown"
        return _STATUS_MAP.get(normalized, normalized.lower())

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
    def _extract_value(data: dict[str, Any], primary: str, fallback: str) -> Any:
        if primary in data and data[primary] is not None:
            return data[primary]
        return data.get(fallback)

    @staticmethod
    def _extract_string(
        data: dict[str, Any], primary: str, fallback: str
    ) -> str | None:
        value = BinanceUserDataGateway._extract_value(data, primary, fallback)
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _extract_float(
        data: dict[str, Any], primary: str, fallback: str, *, default: float
    ) -> float:
        value = BinanceUserDataGateway._extract_value(data, primary, fallback)
        if value is None or value == "":
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _extract_optional_float(
        data: dict[str, Any], primary: str, fallback: str
    ) -> float | None:
        value = BinanceUserDataGateway._extract_value(data, primary, fallback)
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_optional_bool(
        data: dict[str, Any], primary: str, fallback: str
    ) -> bool | None:
        value = BinanceUserDataGateway._extract_value(data, primary, fallback)
        if value is None:
            return None
        return bool(value)

    @staticmethod
    def _normalize_balances(balances: Any) -> dict[str, dict[str, float]]:
        if isinstance(balances, dict):
            normalized: dict[str, dict[str, float]] = {}
            for asset, amounts in balances.items():
                if not isinstance(amounts, dict):
                    continue
                normalized[str(asset).upper()] = {
                    "free": BinanceUserDataGateway._extract_float(
                        amounts, "free", "f", default=0.0
                    ),
                    "locked": BinanceUserDataGateway._extract_float(
                        amounts, "locked", "l", default=0.0
                    ),
                }
            return normalized
        if not isinstance(balances, list):
            return {}
        normalized = {}
        for item in balances:
            if not isinstance(item, dict):
                continue
            asset = str(item.get("a") or item.get("asset") or "").strip().upper()
            if not asset:
                continue
            normalized[asset] = {
                "free": BinanceUserDataGateway._extract_float(
                    item, "free", "f", default=0.0
                ),
                "locked": BinanceUserDataGateway._extract_float(
                    item, "locked", "l", default=0.0
                ),
            }
        return normalized
