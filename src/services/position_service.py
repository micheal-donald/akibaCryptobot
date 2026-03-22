"""Position lifecycle service for execution-driven runtime state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

from domain.events import (
    EventMeta,
    FillPayload,
    OrderUpdatePayload,
    PositionClosedPayload,
    PositionOpenedPayload,
    RuntimeEvent,
)
from domain.state import PositionState, PositionStatus

from .base import BaseService


@dataclass(slots=True)
class PositionService(BaseService):
    """Own position lifecycle state driven by execution events."""

    event_bus: asyncio.Queue[RuntimeEvent]
    _positions: dict[str, PositionState] = field(default_factory=dict, init=False)
    _symbol_to_position_ids: dict[str, str] = field(
        default_factory=dict, init=False, repr=False
    )
    _client_order_to_position_ids: dict[str, str] = field(
        default_factory=dict, init=False, repr=False
    )
    _exchange_order_to_position_ids: dict[str, str] = field(
        default_factory=dict, init=False, repr=False
    )

    async def on_start(self) -> None:
        return None

    async def on_stop(self) -> None:
        return None

    async def handle_execution_event(self, event: RuntimeEvent) -> None:
        """Route an execution event to the relevant state transition."""

        if event.meta.event_type == "execution.order_update":
            await self.handle_order_update_event(event)
        elif event.meta.event_type == "execution.fill":
            await self.handle_fill_event(event)

    async def handle_order_update_event(self, event: RuntimeEvent) -> None:
        """Update entry lifecycle state from an order update event."""

        payload = cast(OrderUpdatePayload, event.payload)
        state = self._resolve_position_state(payload.symbol, payload.client_order_id)
        state.symbol = payload.symbol
        state.side = self._normalize_side(
            payload.symbol, payload.client_order_id, state
        )
        if payload.client_order_id is not None:
            self._client_order_to_position_ids[payload.client_order_id] = (
                state.position_id
            )
        if payload.exchange_order_id is not None:
            self._exchange_order_to_position_ids[payload.exchange_order_id] = (
                state.position_id
            )
        if not payload.reduce_only:
            requested_qty = max(payload.filled_qty + payload.remaining_qty, 0.0)
            if requested_qty > 0:
                state.requested_qty = max(state.requested_qty, requested_qty)
            if (
                payload.exchange_order_id
                and payload.exchange_order_id not in state.entry_order_ids
            ):
                state.entry_order_ids = state.entry_order_ids + (
                    payload.exchange_order_id,
                )
            if payload.filled_qty > 0:
                state.open_qty = max(state.open_qty, payload.filled_qty)
                state.avg_entry_price = payload.avg_fill_price or state.avg_entry_price
                self._mark_open_if_needed(state, event)
            elif state.status in {PositionStatus.NEW, PositionStatus.ENTRY_PENDING}:
                state.status = PositionStatus.ENTRY_PENDING
        state.updated_at = self._now()

    async def handle_fill_event(self, event: RuntimeEvent) -> None:
        """Update position state from a fill event."""

        payload = cast(FillPayload, event.payload)
        state = self._resolve_position_state(payload.symbol, payload.client_order_id)
        state.symbol = payload.symbol
        state.side = payload.side
        if payload.client_order_id is not None:
            self._client_order_to_position_ids[payload.client_order_id] = (
                state.position_id
            )
        if payload.exchange_order_id is not None:
            self._exchange_order_to_position_ids[payload.exchange_order_id] = (
                state.position_id
            )

        fill_qty = max(float(payload.fill_qty), 0.0)
        cumulative_qty = max(float(payload.cumulative_qty), fill_qty)
        previous_open_qty = state.open_qty

        if payload.reduce_only:
            self._apply_exit_fill(
                state,
                payload,
                fill_qty,
                previous_open_qty,
                cumulative_qty,
            )
        else:
            self._apply_entry_fill(
                state,
                payload,
                fill_qty=fill_qty,
                cumulative_qty=cumulative_qty,
                previous_open_qty=previous_open_qty,
                source_event=event,
            )

        state.updated_at = self._now()

    def mark_protective_orders(
        self,
        position_id: str,
        protective_order_ids: tuple[str, ...],
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        trailing_stop: float | None = None,
    ) -> PositionState:
        """Attach exchange-side protective order metadata to a position."""

        state = self._positions[position_id]
        merged_ids = tuple(
            dict.fromkeys((*state.protective_order_ids, *protective_order_ids))
        )
        state.protective_order_ids = merged_ids
        if stop_loss is not None:
            state.stop_loss = stop_loss
        if take_profit is not None:
            state.take_profit = take_profit
        if trailing_stop is not None:
            state.trailing_stop = trailing_stop
        if state.open_qty > 0 and merged_ids:
            state.status = PositionStatus.PROTECTED
        elif state.open_qty > 0:
            state.status = PositionStatus.PROTECTIVE_PENDING
        state.updated_at = self._now()
        return state

    def close_position(
        self,
        position_id: str,
        *,
        close_reason: str,
        avg_exit_price: float | None = None,
        fee_cost: float | None = None,
    ) -> PositionState:
        """Force a position into the closed state and emit a close event."""

        state = self._positions[position_id]
        state.open_qty = 0.0
        if avg_exit_price is not None:
            state.avg_exit_price = avg_exit_price
        if fee_cost is not None:
            state.fees_paid += fee_cost
        state.status = PositionStatus.CLOSED
        state.closed_at = self._now()
        state.updated_at = self._now()
        self._emit_position_closed_event(state, close_reason)
        self._forget_symbol_mapping(state.symbol, position_id)
        return state

    def get_position_state(self, position_id: str) -> PositionState | None:
        return self._positions.get(position_id)

    def get_position_for_symbol(self, symbol: str) -> PositionState | None:
        position_id = self._symbol_to_position_ids.get(symbol)
        if position_id is None:
            return None
        return self._positions.get(position_id)

    def get_all_position_states(self) -> tuple[PositionState, ...]:
        return tuple(self._positions.values())

    def get_unprotected_positions(self) -> tuple[PositionState, ...]:
        return tuple(
            state
            for state in self._positions.values()
            if state.status
            in {
                PositionStatus.OPEN,
                PositionStatus.ENTRY_PENDING,
                PositionStatus.PROTECTIVE_PENDING,
            }
            and state.open_qty > 0
            and not state.protective_order_ids
        )

    def _resolve_position_state(
        self,
        symbol: str,
        client_order_id: str | None,
    ) -> PositionState:
        if client_order_id is not None:
            mapped_position_id = self._client_order_to_position_ids.get(client_order_id)
            if mapped_position_id is not None and mapped_position_id in self._positions:
                return self._positions[mapped_position_id]

        symbol_position_id = self._symbol_to_position_ids.get(symbol)
        if symbol_position_id is not None and symbol_position_id in self._positions:
            return self._positions[symbol_position_id]

        position_id = client_order_id or symbol or uuid4().hex
        state = PositionState(
            position_id=position_id,
            symbol=symbol,
            side="buy",
            requested_qty=0.0,
        )
        self._positions[position_id] = state
        self._symbol_to_position_ids[symbol] = position_id
        if client_order_id is not None:
            self._client_order_to_position_ids[client_order_id] = position_id
        return state

    def _mark_open_if_needed(
        self,
        state: PositionState,
        event: RuntimeEvent,
    ) -> None:
        if state.status in {PositionStatus.CLOSED, PositionStatus.PROTECTED}:
            return
        if state.open_qty <= 0:
            return
        if state.opened_at is None:
            state.opened_at = self._now()
            state.status = (
                PositionStatus.OPEN
                if state.requested_qty <= 0 or state.open_qty >= state.requested_qty
                else PositionStatus.ENTRY_PENDING
            )
            self._emit_position_opened_event(state, event)
            return
        state.status = (
            PositionStatus.OPEN
            if state.requested_qty <= 0 or state.open_qty >= state.requested_qty
            else PositionStatus.ENTRY_PENDING
        )

    def _apply_entry_fill(
        self,
        state: PositionState,
        payload: FillPayload,
        *,
        fill_qty: float,
        cumulative_qty: float,
        previous_open_qty: float,
        source_event: RuntimeEvent,
    ) -> None:
        if (
            payload.exchange_order_id
            and payload.exchange_order_id not in state.entry_order_ids
        ):
            state.entry_order_ids = state.entry_order_ids + (payload.exchange_order_id,)
        if state.requested_qty <= 0:
            state.requested_qty = cumulative_qty
        state.open_qty = max(state.open_qty, cumulative_qty)
        state.avg_entry_price = self._update_average_price(
            state.avg_entry_price,
            previous_qty=previous_open_qty,
            fill_qty=fill_qty,
            fill_price=payload.fill_price,
            cumulative_quote_qty=payload.cumulative_quote_qty,
            cumulative_qty=cumulative_qty,
        )
        self._mark_open_if_needed(state, source_event)
        if state.status != PositionStatus.CLOSED and not state.protective_order_ids:
            state.status = (
                PositionStatus.OPEN
                if state.requested_qty <= 0 or state.open_qty >= state.requested_qty
                else PositionStatus.ENTRY_PENDING
            )

    def _apply_exit_fill(
        self,
        state: PositionState,
        payload: FillPayload,
        fill_qty: float,
        previous_open_qty: float,
        cumulative_qty: float,
    ) -> None:
        if (
            payload.exchange_order_id
            and payload.exchange_order_id not in state.protective_order_ids
        ):
            state.protective_order_ids = state.protective_order_ids + (
                payload.exchange_order_id,
            )
        state.avg_exit_price = self._update_average_price(
            state.avg_exit_price,
            previous_qty=max(previous_open_qty, 0.0),
            fill_qty=fill_qty,
            fill_price=payload.fill_price,
            cumulative_quote_qty=payload.cumulative_quote_qty,
            cumulative_qty=cumulative_qty,
        )
        state.fees_paid += float(payload.fee_cost or 0.0)
        state.open_qty = max(previous_open_qty - fill_qty, 0.0)
        if state.open_qty <= 0:
            state.status = PositionStatus.CLOSED
            state.closed_at = self._now()
            self._emit_position_closed_event(
                state,
                "reduce_only_fill",
                fee_cost=payload.fee_cost,
            )
            self._forget_symbol_mapping(state.symbol, state.position_id)
        else:
            state.status = PositionStatus.REDUCE_PENDING

    def _emit_position_opened_event(
        self,
        state: PositionState,
        source_event: RuntimeEvent,
    ) -> None:
        payload = PositionOpenedPayload(
            position_id=state.position_id,
            symbol=state.symbol,
            side=self._normalize_position_side(state.side),
            quantity=state.open_qty,
            avg_entry_price=state.avg_entry_price or 0.0,
            entry_order_ids=state.entry_order_ids,
        )
        self.event_bus.put_nowait(
            RuntimeEvent(
                meta=EventMeta(
                    event_id=uuid4().hex,
                    event_type="position.opened",
                    source="position.service",
                    symbol=state.symbol,
                    ts_exchange=source_event.meta.ts_exchange,
                    ts_received=self._now(),
                ),
                payload=payload,
            )
        )

    def _emit_position_closed_event(
        self,
        state: PositionState,
        close_reason: str,
        *,
        fee_cost: float | None = None,
    ) -> None:
        payload = PositionClosedPayload(
            position_id=state.position_id,
            symbol=state.symbol,
            side=self._normalize_position_side(state.side),
            quantity=state.requested_qty or state.open_qty,
            avg_entry_price=state.avg_entry_price,
            avg_exit_price=state.avg_exit_price,
            close_reason=close_reason,
            pnl=None,
            fee_cost=fee_cost,
        )
        self.event_bus.put_nowait(
            RuntimeEvent(
                meta=EventMeta(
                    event_id=uuid4().hex,
                    event_type="position.closed",
                    source="position.service",
                    symbol=state.symbol,
                    ts_exchange=None,
                    ts_received=self._now(),
                ),
                payload=payload,
            )
        )

    def _forget_symbol_mapping(self, symbol: str, position_id: str) -> None:
        if self._symbol_to_position_ids.get(symbol) == position_id:
            self._symbol_to_position_ids.pop(symbol, None)

    @staticmethod
    def _normalize_position_side(side: str) -> str:
        normalized = str(side).strip().lower()
        return normalized if normalized in {"buy", "sell"} else "buy"

    @staticmethod
    def _normalize_side(
        symbol: str,
        client_order_id: str | None,
        state: PositionState,
    ) -> str:
        del symbol, client_order_id
        return PositionService._normalize_position_side(state.side)

    @staticmethod
    def _update_average_price(
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
