"""Execution service for order submission and exchange event tracking."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, cast
from enum import StrEnum
from uuid import uuid4

from domain.events import (
    EventMeta,
    FillPayload,
    OrderIntentPayload,
    OrderRejectedPayload,
    OrderSubmittedPayload,
    OrderUpdatePayload,
    ProtectionSubmittedPayload,
    RuntimeEvent,
)
from domain.state import OrderState, OrderStatus
from ports.exchange import ExecutionGateway, ProtectionIntent, UserStreamGateway

from .base import BaseService


class ProtectionStatus(StrEnum):
    """Runtime protection outcome for an entry order."""

    PENDING = "pending"
    NATIVE_SUBMITTED = "native_submitted"
    DEGRADED = "degraded"


@dataclass(slots=True)
class ProtectionState:
    """Mutable native protection outcome for a submitted entry order."""

    client_order_id: str
    symbol: str
    requested_qty: float
    stop_loss: float | None
    take_profit: float | None
    trailing_stop: float | None
    status: ProtectionStatus = ProtectionStatus.PENDING
    mode: str = "native"
    submitted_order_ids: tuple[str, ...] = ()
    reason: str | None = None
    attempted_at: datetime | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
    _order_intents: dict[str, OrderIntentPayload] = field(
        default_factory=dict, init=False, repr=False
    )
    _exchange_to_client_order_ids: dict[str, str] = field(
        default_factory=dict, init=False, repr=False
    )
    _protection_states: dict[str, ProtectionState] = field(
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
                    payload=OrderRejectedPayload(
                        intent_id=intent.intent_id,
                        symbol=intent.symbol,
                        reason=str(exc),
                    ),
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
        self._order_intents[intent.intent_id] = intent

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

    def restore_order_from_trade(
        self,
        trade: dict[str, Any],
        *,
        order_snapshot: OrderUpdatePayload | None = None,
    ) -> OrderState:
        """Rehydrate a persisted open trade into execution state."""

        client_order_id = str(trade.get("order_id") or trade.get("symbol"))
        if not client_order_id:
            raise ValueError("trade must provide symbol or order_id")

        symbol = str(trade["symbol"])
        side = str(trade["side"])
        requested_qty = float(trade["amount"])
        intent = OrderIntentPayload(
            intent_id=client_order_id,
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            order_type="market",
            quantity=requested_qty,
            stop_loss=self._optional_float(trade.get("stop_loss")),
            take_profit=self._optional_float(trade.get("take_profit")),
            reduce_only=False,
            reason="recovered_from_persistence",
        )
        self._order_intents[client_order_id] = intent

        state = self._order_states.get(client_order_id)
        if state is None:
            state = OrderState(
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                order_type="market",
                requested_qty=requested_qty,
            )
            self._order_states[client_order_id] = state

        state.order_id = str(trade.get("order_id") or client_order_id)
        state.symbol = symbol
        state.side = side
        state.order_type = "market"
        state.requested_qty = requested_qty
        state.requested_price = None
        state.reduce_only = False
        state.status = OrderStatus.FILLED
        state.filled_qty = requested_qty
        state.avg_fill_price = self._optional_float(trade.get("entry_price"))
        state.updated_at = self._now()

        if order_snapshot is not None:
            state.order_id = order_snapshot.exchange_order_id or state.order_id
            state.status = self._coerce_status(
                order_snapshot.status,
                default=OrderStatus.FILLED,
            )
            state.filled_qty = max(state.filled_qty, float(order_snapshot.filled_qty))
            state.avg_fill_price = order_snapshot.avg_fill_price or state.avg_fill_price
            if order_snapshot.exchange_order_id is not None:
                self._exchange_to_client_order_ids[order_snapshot.exchange_order_id] = (
                    client_order_id
                )

        self._submitted_orders[client_order_id] = OrderSubmittedPayload(
            client_order_id=client_order_id,
            exchange_order_id=state.order_id,
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            order_type=state.order_type,
            requested_qty=requested_qty,
            requested_price=None,
            reduce_only=False,
        )
        return state

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
        await self._maybe_submit_native_protection(state)

    async def _handle_fill_event(self, event: RuntimeEvent) -> None:
        payload = cast(FillPayload, event.payload)
        state = self._resolve_order_state(payload)
        state.order_id = payload.exchange_order_id or state.order_id
        state.symbol = payload.symbol
        state.side = payload.side
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
        await self._maybe_submit_native_protection(state)

    async def _handle_balance_update_event(self, event: RuntimeEvent) -> None:
        await self._emit_event(event)

    async def _emit_event(self, event: RuntimeEvent) -> None:
        await self.event_bus.put(event)

    async def _disconnect_gateways(self) -> None:
        if self.user_stream_gateway is not None:
            await self.user_stream_gateway.disconnect()
        await self.execution_gateway.disconnect()

    async def ensure_native_protection(
        self,
        client_order_id: str,
        *,
        force: bool = False,
    ) -> ProtectionState | None:
        """Attempt native protection for a known order state."""

        state = self._order_states.get(client_order_id)
        if state is None:
            return None
        await self._maybe_submit_native_protection(state, force=force)
        return self._protection_states.get(client_order_id)

    def mark_order_orphaned(self, client_order_id: str, reason: str) -> OrderState:
        """Flag a recovered order as orphaned after exchange reconciliation."""

        state = self._order_states.get(client_order_id)
        if state is None:
            raise KeyError(client_order_id)
        state.status = OrderStatus.ORPHANED
        state.updated_at = self._now()
        if reason:
            self.logger.warning(
                "Recovered order %s marked orphaned: %s", client_order_id, reason
            )
        return state

    async def _maybe_submit_native_protection(
        self,
        state: OrderState,
        *,
        force: bool = False,
    ) -> None:
        if (not force and state.status != OrderStatus.FILLED) or state.reduce_only:
            return

        intent = self._order_intents.get(state.client_order_id)
        if intent is None:
            return

        protection = self._build_protection_intent(intent)
        if protection is None:
            return

        existing = self._protection_states.get(state.client_order_id)
        if existing is not None and existing.status != ProtectionStatus.PENDING:
            return

        protection_state = ProtectionState(
            client_order_id=state.client_order_id,
            symbol=state.symbol,
            requested_qty=max(state.filled_qty, state.requested_qty),
            stop_loss=protection.stop_loss_price,
            take_profit=protection.take_profit_price,
            trailing_stop=protection.trailing_stop_price,
            attempted_at=self._now(),
        )

        try:
            submitted_orders = await self.execution_gateway.submit_protection(
                state.symbol,
                state.side,
                protection_state.requested_qty,
                protection,
            )
        except Exception as exc:
            protection_state.status = ProtectionStatus.DEGRADED
            protection_state.mode = "degraded"
            protection_state.reason = str(exc)
            protection_state.updated_at = self._now()
            self._protection_states[state.client_order_id] = protection_state
            await self._emit_protective_submission_event(protection_state)
            return

        protection_state.submitted_order_ids = tuple(
            order.exchange_order_id
            for order in submitted_orders
            if order.exchange_order_id is not None
        )
        requested_count = self._requested_protection_count(protection)
        if len(protection_state.submitted_order_ids) >= requested_count:
            protection_state.status = ProtectionStatus.NATIVE_SUBMITTED
            protection_state.mode = "native"
            protection_state.reason = None
        else:
            protection_state.status = ProtectionStatus.DEGRADED
            protection_state.mode = "degraded"
            protection_state.reason = (
                "native protection partially submitted"
                if protection_state.submitted_order_ids
                else "native protection unavailable"
            )
        protection_state.updated_at = self._now()
        self._protection_states[state.client_order_id] = protection_state
        await self._emit_protective_submission_event(protection_state)

    async def _emit_protective_submission_event(
        self, protection_state: ProtectionState
    ) -> None:
        await self._emit_event(
            RuntimeEvent(
                meta=EventMeta(
                    event_id=uuid4().hex,
                    event_type="position.protective_submitted",
                    source="execution.service",
                    symbol=protection_state.symbol,
                    ts_exchange=None,
                    ts_received=self._now(),
                ),
                payload=ProtectionSubmittedPayload(
                    client_order_id=protection_state.client_order_id,
                    symbol=protection_state.symbol,
                    status=protection_state.status.value,  # type: ignore[arg-type]
                    mode=protection_state.mode,  # type: ignore[arg-type]
                    requested_qty=protection_state.requested_qty,
                    submitted_order_ids=protection_state.submitted_order_ids,
                    reason=protection_state.reason,
                    stop_loss=protection_state.stop_loss,
                    take_profit=protection_state.take_profit,
                    trailing_stop=protection_state.trailing_stop,
                    attempted_at=protection_state.attempted_at,
                    updated_at=protection_state.updated_at,
                ),
            )
        )

    @staticmethod
    def _requested_protection_count(protection: ProtectionIntent) -> int:
        return sum(
            value is not None
            for value in (
                protection.stop_loss_price,
                protection.take_profit_price,
                protection.trailing_stop_price,
            )
        )

    @staticmethod
    def _build_protection_intent(
        intent: OrderIntentPayload,
    ) -> ProtectionIntent | None:
        if (
            intent.stop_loss is None
            and intent.take_profit is None
            and not intent.reduce_only
        ):
            return None
        return ProtectionIntent(
            stop_loss_price=intent.stop_loss,
            take_profit_price=intent.take_profit,
            trailing_stop_price=None,
            native_preferred=True,
        )

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

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

    def get_protection_state(self, client_order_id: str) -> ProtectionState | None:
        """Return the last known native protection outcome for an order."""

        return self._protection_states.get(client_order_id)
