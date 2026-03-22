"""Execution service for order submission and exchange event tracking."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

from domain.events import (
    EventMeta,
    FillPayload,
    OrderIntentPayload,
    OrderSubmittedPayload,
    OrderUpdatePayload,
    RuntimeEvent,
)
from domain.state import OrderState, OrderStatus
from ports.exchange import ExecutionGateway, UserStreamGateway

from .base import BaseService


@dataclass(slots=True)
class ExecutionService(BaseService):
    """Submit orders and keep order state synchronized with exchange updates."""

    execution_gateway: ExecutionGateway
    event_bus: asyncio.Queue[RuntimeEvent]
    user_stream_gateway: UserStreamGateway | None = None
    _order_states: dict[str, OrderState] = field(default_factory=dict, init=False)
    _submitted_orders: dict[str, OrderSubmittedPayload] = field(
        default_factory=dict, init=False, repr=False
    )
    _exchange_to_client_order_ids: dict[str, str] = field(
        default_factory=dict, init=False, repr=False
    )
    _callbacks_registered: bool = field(default=False, init=False, repr=False)

    async def on_start(self) -> None:
        self._register_user_stream_callbacks()
        try:
            await self.execution_gateway.connect()
            if self.user_stream_gateway is not None:
                await self.user_stream_gateway.connect()
                await self.user_stream_gateway.subscribe_user_stream()
        except Exception:
            await self._disconnect_gateways()
            raise

    async def on_stop(self) -> None:
        await self._disconnect_gateways()

    async def submit_order_intent(
        self, intent: OrderIntentPayload
    ) -> OrderSubmittedPayload:
        """Submit an order intent and emit a normalized submission event."""

        existing = self._submitted_orders.get(intent.intent_id)
        if existing is not None:
            return existing

        state = self._order_states.get(intent.intent_id)
        if state is None:
            state = self._create_order_state(intent)
            self._order_states[intent.intent_id] = state
        state.status = OrderStatus.SUBMIT_PENDING
        state.updated_at = self._now()

        try:
            submitted = await self.execution_gateway.submit_order(intent)
        except Exception as exc:
            state.status = OrderStatus.REJECTED
            state.updated_at = self._now()
            await self._emit_event(
                RuntimeEvent(
                    meta=EventMeta(
                        event_id=uuid4().hex,
                        event_type="execution.order_rejected",
                        source="execution.service",
                        symbol=intent.symbol,
                        ts_exchange=None,
                        ts_received=self._now(),
                    ),
                    payload={
                        "intent_id": intent.intent_id,
                        "symbol": intent.symbol,
                        "reason": str(exc),
                    },
                )
            )
            raise

        state.order_id = submitted.exchange_order_id
        state.symbol = submitted.symbol
        state.side = submitted.side
        state.order_type = submitted.order_type
        state.requested_qty = submitted.requested_qty
        state.requested_price = submitted.requested_price
        state.reduce_only = submitted.reduce_only
        state.status = OrderStatus.ACCEPTED
        state.updated_at = self._now()

        if submitted.exchange_order_id is not None:
            self._exchange_to_client_order_ids[submitted.exchange_order_id] = (
                intent.intent_id
            )
        self._submitted_orders[intent.intent_id] = submitted

        await self._emit_event(
            RuntimeEvent(
                meta=EventMeta(
                    event_id=uuid4().hex,
                    event_type="execution.order_submitted",
                    source="execution.service",
                    symbol=submitted.symbol,
                    ts_exchange=None,
                    ts_received=self._now(),
                ),
                payload=submitted,
            )
        )
        return submitted

    def get_order_state(self, client_order_id: str) -> OrderState | None:
        """Return the live state for a client order id."""

        return self._order_states.get(client_order_id)

    def get_all_order_states(self) -> tuple[OrderState, ...]:
        """Return a snapshot of all tracked orders."""

        return tuple(self._order_states.values())

    def _register_user_stream_callbacks(self) -> None:
        if self._callbacks_registered or self.user_stream_gateway is None:
            return
        self.user_stream_gateway.register_order_update_handler(
            self._handle_order_update_event
        )
        self.user_stream_gateway.register_fill_handler(self._handle_fill_event)
        self.user_stream_gateway.register_balance_update_handler(
            self._handle_balance_update_event
        )
        self._callbacks_registered = True

    async def _handle_order_update_event(self, event: RuntimeEvent) -> None:
        payload = cast(OrderUpdatePayload, event.payload)
        state = self._resolve_order_state(payload)
        state.order_id = payload.exchange_order_id or state.order_id
        if payload.client_order_id is not None:
            self._submitted_orders.setdefault(
                payload.client_order_id,
                OrderSubmittedPayload(
                    client_order_id=payload.client_order_id,
                    exchange_order_id=payload.exchange_order_id,
                    symbol=payload.symbol,
                    side=state.side,  # type: ignore[arg-type]
                    order_type=state.order_type,
                    requested_qty=state.requested_qty,
                    requested_price=state.requested_price,
                    reduce_only=payload.reduce_only,
                ),
            )
        state.status = self._coerce_status(payload.status, default=state.status)
        state.filled_qty = max(state.filled_qty, float(payload.filled_qty))
        state.avg_fill_price = payload.avg_fill_price or state.avg_fill_price
        state.reduce_only = payload.reduce_only
        state.updated_at = self._now()
        if (
            payload.exchange_order_id is not None
            and payload.client_order_id is not None
        ):
            self._exchange_to_client_order_ids[payload.exchange_order_id] = (
                payload.client_order_id
            )
        await self._emit_event(event)

    async def _handle_fill_event(self, event: RuntimeEvent) -> None:
        payload = cast(FillPayload, event.payload)
        state = self._resolve_order_state(payload)
        previous_filled_qty = state.filled_qty
        cumulative_qty = max(float(payload.cumulative_qty), float(payload.fill_qty))
        state.filled_qty = max(state.filled_qty, cumulative_qty)
        state.avg_fill_price = self._compute_average_fill_price(
            state.avg_fill_price,
            previous_qty=previous_filled_qty,
            fill_qty=payload.fill_qty,
            fill_price=payload.fill_price,
            cumulative_quote_qty=payload.cumulative_quote_qty,
            cumulative_qty=cumulative_qty,
        )
        state.status = (
            OrderStatus.FILLED
            if state.requested_qty > 0 and state.filled_qty >= state.requested_qty
            else OrderStatus.PARTIALLY_FILLED
        )
        state.reduce_only = payload.reduce_only
        state.updated_at = self._now()
        if (
            payload.exchange_order_id is not None
            and payload.client_order_id is not None
        ):
            self._exchange_to_client_order_ids[payload.exchange_order_id] = (
                payload.client_order_id
            )
        await self._emit_event(event)

    async def _handle_balance_update_event(self, event: RuntimeEvent) -> None:
        await self._emit_event(event)

    async def _emit_event(self, event: RuntimeEvent) -> None:
        await self.event_bus.put(event)

    async def _disconnect_gateways(self) -> None:
        if self.user_stream_gateway is not None:
            await self.user_stream_gateway.disconnect()
        await self.execution_gateway.disconnect()

    def _create_order_state(self, intent: OrderIntentPayload) -> OrderState:
        return OrderState(
            client_order_id=intent.intent_id,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            requested_qty=intent.quantity,
            requested_price=intent.limit_price,
            reduce_only=intent.reduce_only,
        )

    def _resolve_order_state(
        self, payload: OrderUpdatePayload | FillPayload
    ) -> OrderState:
        client_order_id = getattr(payload, "client_order_id", None)
        exchange_order_id = getattr(payload, "exchange_order_id", None)

        if client_order_id is not None and client_order_id in self._order_states:
            return self._order_states[client_order_id]

        if exchange_order_id is not None:
            mapped_client_id = self._exchange_to_client_order_ids.get(exchange_order_id)
            if mapped_client_id is not None and mapped_client_id in self._order_states:
                return self._order_states[mapped_client_id]

        fallback_client_id = client_order_id or exchange_order_id or uuid4().hex
        existing = self._order_states.get(fallback_client_id)
        if existing is not None:
            return existing

        requested_qty = self._derive_requested_qty(payload)
        state = OrderState(
            client_order_id=fallback_client_id,
            symbol=payload.symbol,
            side=getattr(payload, "side", "buy"),
            order_type="market",
            requested_qty=requested_qty,
            order_id=exchange_order_id,
        )
        self._order_states[fallback_client_id] = state
        if exchange_order_id is not None:
            self._exchange_to_client_order_ids[exchange_order_id] = fallback_client_id
        return state

    @staticmethod
    def _derive_requested_qty(payload: OrderUpdatePayload | FillPayload) -> float:
        if isinstance(payload, FillPayload):
            return max(float(payload.cumulative_qty), float(payload.fill_qty))
        return max(float(payload.filled_qty) + float(payload.remaining_qty), 0.0)

    @staticmethod
    def _coerce_status(
        status: str,
        *,
        default: OrderStatus,
    ) -> OrderStatus:
        normalized = str(status).strip().lower()
        if not normalized:
            return default
        try:
            return OrderStatus(normalized)
        except ValueError:
            return default

    @staticmethod
    def _compute_average_fill_price(
        current_avg: float | None,
        *,
        previous_qty: float,
        fill_qty: float,
        fill_price: float,
        cumulative_quote_qty: float | None,
        cumulative_qty: float,
    ) -> float:
        if cumulative_quote_qty is not None and cumulative_qty > 0:
            return cumulative_quote_qty / cumulative_qty
        if current_avg is None or previous_qty <= 0:
            return fill_price
        total_qty = previous_qty + fill_qty
        if total_qty <= 0:
            return fill_price
        return ((current_avg * previous_qty) + (fill_price * fill_qty)) / total_qty

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
