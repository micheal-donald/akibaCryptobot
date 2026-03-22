"""Binance REST execution adapter for normalized order lifecycle handling."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from adapters.binance.symbol_mapper import BinanceSymbolMapper
from domain.events import OrderIntentPayload, OrderSubmittedPayload, OrderUpdatePayload
from exchange.binance_client import BinanceClient
from ports.exchange import ExecutionGateway, ProtectionIntent

logger = logging.getLogger(__name__)
_SYMBOL_MAPPER = BinanceSymbolMapper()


@runtime_checkable
class _BinanceExchangeLike(Protocol):
    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any: ...


@runtime_checkable
class _ExecutionClientLike(Protocol):
    exchange: _BinanceExchangeLike

    async def ensure_markets_loaded(self) -> None: ...

    async def place_market_order(
        self, symbol: str, side: str, amount: float, price: float | None = None
    ) -> Any: ...

    async def place_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> Any: ...

    async def cancel_order(self, order_id: str, symbol: str) -> Any: ...

    async def get_order_status(self, order_id: str, symbol: str) -> Any: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class BinanceExecutionGateway(ExecutionGateway):
    """Submit and normalize Binance execution lifecycle operations."""

    client: _ExecutionClientLike
    close_on_disconnect: bool = False

    def __init__(
        self,
        client: _ExecutionClientLike | None = None,
        *,
        api_key: str = "",
        api_secret: str = "",
        sandbox: bool = True,
        market_type: str = "spot",
        binance_us: bool = False,
        close_on_disconnect: bool = False,
    ) -> None:
        if client is None:
            client = BinanceClient(
                api_key=api_key,
                secret_key=api_secret,
                sandbox=sandbox,
                market_type=market_type,
                binance_us=binance_us,
            )
        self.client = client
        self.close_on_disconnect = close_on_disconnect

    async def connect(self) -> None:
        await self.client.ensure_markets_loaded()

    async def disconnect(self) -> None:
        if self.close_on_disconnect:
            await self.client.close()

    async def submit_order(self, intent: OrderIntentPayload) -> OrderSubmittedPayload:
        symbol = self._normalize_symbol(intent.symbol)
        side = self._normalize_side(intent.side)
        raw_order = await self._submit_order(symbol, side, intent)
        return self._normalize_submitted_order(symbol, side, intent, raw_order)

    async def cancel_order(
        self, symbol: str, exchange_order_id: str
    ) -> OrderUpdatePayload:
        normalized_symbol = self._normalize_symbol(symbol)
        raw_order = await self.client.cancel_order(exchange_order_id, normalized_symbol)
        return self._normalize_order_update(
            normalized_symbol,
            raw_order,
            default_status="cancelled",
        )

    async def fetch_order(
        self, symbol: str, exchange_order_id: str
    ) -> OrderUpdatePayload:
        normalized_symbol = self._normalize_symbol(symbol)
        raw_order = await self.client.get_order_status(
            exchange_order_id, normalized_symbol
        )
        return self._normalize_order_update(normalized_symbol, raw_order)

    async def submit_protection(
        self,
        symbol: str,
        side: str,
        quantity: float,
        protection: ProtectionIntent,
    ) -> tuple[OrderSubmittedPayload, ...]:
        normalized_symbol = self._normalize_symbol(symbol)
        exit_side = self._opposite_side(side)
        submitted_orders: list[OrderSubmittedPayload] = []

        if protection.stop_loss_price is not None:
            submitted_orders.extend(
                await self._submit_protection_order(
                    normalized_symbol,
                    exit_side,
                    quantity,
                    order_type="stop_loss_limit",
                    trigger_price=protection.stop_loss_price,
                    best_effort_label="stop loss",
                )
            )

        if protection.take_profit_price is not None:
            submitted_orders.extend(
                await self._submit_protection_order(
                    normalized_symbol,
                    exit_side,
                    quantity,
                    order_type="take_profit_limit",
                    trigger_price=protection.take_profit_price,
                    best_effort_label="take profit",
                )
            )

        if protection.trailing_stop_price is not None:
            submitted_orders.extend(
                await self._submit_protection_order(
                    normalized_symbol,
                    exit_side,
                    quantity,
                    order_type="trailing_stop_market",
                    trigger_price=protection.trailing_stop_price,
                    best_effort_label="trailing stop",
                )
            )

        return tuple(submitted_orders)

    async def _submit_order(
        self, symbol: str, side: str, intent: OrderIntentPayload
    ) -> Any:
        if intent.order_type == "market":
            return await self.client.place_market_order(
                symbol,
                side,
                intent.quantity,
                price=intent.limit_price,
            )
        if intent.order_type == "limit":
            if intent.limit_price is None:
                raise ValueError("limit orders require limit_price")
            return await self.client.place_limit_order(
                symbol,
                side,
                intent.quantity,
                intent.limit_price,
            )
        raise ValueError(f"unsupported order type: {intent.order_type}")

    async def _submit_protection_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        *,
        order_type: str,
        trigger_price: float,
        best_effort_label: str,
    ) -> tuple[OrderSubmittedPayload, ...]:
        price = None if order_type == "trailing_stop_market" else trigger_price
        try:
            raw_order = await self.client.exchange.create_order(
                symbol,
                order_type,
                side,
                quantity,
                price,
                params={
                    "stopPrice": trigger_price,
                    "activationPrice": trigger_price,
                    "reduceOnly": True,
                },
            )
        except Exception as exc:
            logger.warning(
                "Binance protection order best-effort failed for %s %s: %s",
                symbol,
                best_effort_label,
                exc,
            )
            return ()

        return (
            self._normalize_submitted_order(
                symbol,
                side,
                OrderIntentPayload(
                    intent_id=f"protect-{symbol}-{order_type}",
                    symbol=symbol,
                    side=side,  # type: ignore[arg-type]
                    order_type="limit",
                    quantity=quantity,
                    limit_price=trigger_price,
                    reduce_only=True,
                ),
                raw_order,
                order_type=order_type,
            ),
        )

    def _normalize_submitted_order(
        self,
        symbol: str,
        side: str,
        intent: OrderIntentPayload,
        raw_order: Any,
        *,
        order_type: str | None = None,
    ) -> OrderSubmittedPayload:
        return OrderSubmittedPayload(
            client_order_id=self._extract_client_order_id(raw_order, intent.intent_id),
            exchange_order_id=self._extract_order_id(raw_order),
            symbol=self._extract_symbol(raw_order, symbol),
            side=side,
            order_type=order_type or intent.order_type,
            requested_qty=float(
                self._extract_value(raw_order, "amount", default=intent.quantity)
            ),
            requested_price=self._extract_requested_price(
                raw_order, intent.limit_price
            ),
            reduce_only=bool(
                self._extract_value(raw_order, "reduceOnly", default=intent.reduce_only)
            ),
        )

    def _normalize_order_update(
        self,
        symbol: str,
        raw_order: Any,
        *,
        default_status: str | None = None,
    ) -> OrderUpdatePayload:
        requested_qty = self._extract_float(raw_order, "amount", default=0.0)
        filled_qty = self._extract_float(
            raw_order,
            "filled",
            default=self._extract_float(raw_order, "filledQty", default=0.0),
        )
        remaining_qty = self._extract_float(
            raw_order,
            "remaining",
            default=max(requested_qty - filled_qty, 0.0),
        )
        return OrderUpdatePayload(
            client_order_id=self._extract_client_order_id(raw_order, None),
            exchange_order_id=self._extract_order_id(raw_order),
            symbol=self._extract_symbol(raw_order, symbol),
            status=self._extract_status(raw_order, default_status=default_status),
            filled_qty=filled_qty,
            remaining_qty=remaining_qty,
            avg_fill_price=self._extract_optional_float(
                raw_order, "average", "avgPrice", "price"
            ),
            reduce_only=bool(
                self._extract_value(raw_order, "reduceOnly", default=False)
            ),
            raw_status=self._extract_raw_status(raw_order),
        )

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        text = str(symbol).strip()
        if not text:
            raise ValueError("symbol must not be empty")
        if "/" in text:
            return _SYMBOL_MAPPER.to_canonical_symbol(
                _SYMBOL_MAPPER.to_exchange_symbol(text)
            )
        return _SYMBOL_MAPPER.to_canonical_symbol(text)

    @staticmethod
    def _normalize_side(side: str) -> str:
        normalized = str(side).strip().lower()
        if normalized not in {"buy", "sell"}:
            raise ValueError(f"unsupported side: {side!r}")
        return normalized

    @staticmethod
    def _opposite_side(side: str) -> str:
        normalized = BinanceExecutionGateway._normalize_side(side)
        return "sell" if normalized == "buy" else "buy"

    @staticmethod
    def _extract_order_id(raw_order: Any) -> str | None:
        if isinstance(raw_order, dict):
            return raw_order.get("id") or raw_order.get("orderId")
        return getattr(raw_order, "id", None) or getattr(raw_order, "orderId", None)

    @staticmethod
    def _extract_client_order_id(raw_order: Any, fallback: str | None) -> str | None:
        if isinstance(raw_order, dict):
            return raw_order.get("clientOrderId") or fallback
        return getattr(raw_order, "clientOrderId", None) or fallback

    @staticmethod
    def _extract_symbol(raw_order: Any, fallback: str) -> str:
        if isinstance(raw_order, dict):
            return raw_order.get("symbol", fallback)
        return getattr(raw_order, "symbol", fallback)

    @staticmethod
    def _extract_requested_price(
        raw_order: Any, fallback: float | None
    ) -> float | None:
        value = BinanceExecutionGateway._extract_value(raw_order, "price", default=None)
        if value is None:
            value = BinanceExecutionGateway._extract_value(
                raw_order, "requestedPrice", default=None
            )
        return float(value) if value is not None else fallback

    @staticmethod
    def _extract_float(raw_order: Any, key: str, *, default: float) -> float:
        value = BinanceExecutionGateway._extract_value(raw_order, key, default=None)
        if value is None:
            return default
        return float(value)

    @staticmethod
    def _extract_optional_float(raw_order: Any, *keys: str) -> float | None:
        for key in keys:
            value = BinanceExecutionGateway._extract_value(raw_order, key, default=None)
            if value is not None:
                return float(value)
        return None

    @staticmethod
    def _extract_status(raw_order: Any, *, default_status: str | None = None) -> str:
        value = BinanceExecutionGateway._extract_value(
            raw_order, "status", default=None
        )
        return str(value or default_status or "unknown")

    @staticmethod
    def _extract_raw_status(raw_order: Any) -> str | None:
        value = BinanceExecutionGateway._extract_value(
            raw_order, "raw_status", default=None
        )
        return str(value) if value is not None else None

    @staticmethod
    def _extract_value(raw_order: Any, key: str, *, default: Any) -> Any:
        if isinstance(raw_order, dict):
            return raw_order.get(key, default)
        return getattr(raw_order, key, default)
