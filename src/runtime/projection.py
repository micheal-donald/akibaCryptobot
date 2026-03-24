"""Read-only projection helpers for runtime-owned operator state."""

from __future__ import annotations

from domain.state import OrderState, OrderStatus, PositionState, PositionStatus
from runtime.mode import RuntimeMode

from .view_models import (
    ProjectedOrderView,
    ProjectedPositionView,
    ProjectionSource,
    RuntimeProjection,
)

_ACTIVE_POSITION_STATUSES = {
    PositionStatus.ENTRY_PENDING,
    PositionStatus.OPEN,
    PositionStatus.PROTECTIVE_PENDING,
    PositionStatus.PROTECTED,
    PositionStatus.REDUCE_PENDING,
}

_IN_FLIGHT_ORDER_STATUSES = {
    OrderStatus.CREATED,
    OrderStatus.SUBMIT_PENDING,
    OrderStatus.ACCEPTED,
    OrderStatus.PARTIALLY_FILLED,
    OrderStatus.CANCEL_PENDING,
}


class PositionStateSource:
    """Protocol-style base for runtime position readers."""

    def get_all_position_states(self) -> tuple[PositionState, ...]: ...


class OrderStateSource:
    """Protocol-style base for runtime order readers."""

    def get_all_order_states(self) -> tuple[OrderState, ...]: ...


def build_runtime_projection(
    *,
    runtime_mode: RuntimeMode | str,
    runtime_is_running: bool,
    position_source: PositionStateSource | None = None,
    execution_source: OrderStateSource | None = None,
) -> RuntimeProjection:
    """Build a stable, read-only summary of runtime-managed state."""

    mode = RuntimeMode.parse(runtime_mode)
    positions = tuple(
        _project_position(state) for state in _sorted_positions(position_source)
    )
    orders = tuple(_project_order(state) for state in _sorted_orders(execution_source))
    runtime_enabled = mode.runtime_enabled

    return RuntimeProjection(
        source=_projection_source(mode),
        runtime_mode=mode.value,
        runtime_enabled=runtime_enabled,
        runtime_is_running=runtime_is_running,
        runtime_is_healthy=(not runtime_enabled) or runtime_is_running,
        runtime_owns_entries=mode.owns_entries,
        runtime_owns_exits=mode.owns_exits,
        active_position_count=sum(
            1 for state in positions if _is_active_position(state)
        ),
        protected_position_count=sum(
            1 for state in positions if state.status == PositionStatus.PROTECTED.value
        ),
        pending_exit_count=sum(
            1
            for state in positions
            if state.status == PositionStatus.REDUCE_PENDING.value
        ),
        open_order_count=sum(
            1 for state in orders if state.status in _in_flight_status_values()
        ),
        rejected_order_count=sum(
            1 for state in orders if state.status == OrderStatus.REJECTED.value
        ),
        positions=positions,
        orders=orders,
    )


def _projection_source(mode: RuntimeMode) -> ProjectionSource:
    if mode is RuntimeMode.SHADOW:
        return ProjectionSource.SHADOW
    if mode.owns_entries or mode.owns_exits:
        return ProjectionSource.RUNTIME_PROJECTION
    return ProjectionSource.LEGACY


def _sorted_positions(source: PositionStateSource | None) -> tuple[PositionState, ...]:
    if source is None or not hasattr(source, "get_all_position_states"):
        return ()
    states = source.get_all_position_states()
    return tuple(sorted(states, key=lambda state: (state.symbol, state.position_id)))


def _sorted_orders(source: OrderStateSource | None) -> tuple[OrderState, ...]:
    if source is None or not hasattr(source, "get_all_order_states"):
        return ()
    states = source.get_all_order_states()
    return tuple(
        sorted(states, key=lambda state: (state.symbol, state.client_order_id))
    )


def _project_position(state: PositionState) -> ProjectedPositionView:
    return ProjectedPositionView(
        position_id=state.position_id,
        symbol=state.symbol,
        side=state.side,
        status=state.status.value,
        open_qty=state.open_qty,
        requested_qty=state.requested_qty,
        avg_entry_price=state.avg_entry_price,
        avg_exit_price=state.avg_exit_price,
        stop_loss=state.stop_loss,
        take_profit=state.take_profit,
        trailing_stop=state.trailing_stop,
        entry_order_ids=state.entry_order_ids,
        protective_order_ids=state.protective_order_ids,
        fees_paid=state.fees_paid,
        opened_at=state.opened_at,
        closed_at=state.closed_at,
        updated_at=state.updated_at,
    )


def _project_order(state: OrderState) -> ProjectedOrderView:
    return ProjectedOrderView(
        client_order_id=state.client_order_id,
        exchange_order_id=state.order_id,
        symbol=state.symbol,
        side=state.side,
        order_type=state.order_type,
        status=state.status.value,
        requested_qty=state.requested_qty,
        filled_qty=state.filled_qty,
        avg_fill_price=state.avg_fill_price,
        reduce_only=state.reduce_only,
        parent_position_id=state.parent_position_id,
        requested_price=state.requested_price,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


def _is_active_position(state: ProjectedPositionView) -> bool:
    return state.open_qty > 0 and state.status in _active_position_status_values()


def _active_position_status_values() -> set[str]:
    return {status.value for status in _ACTIVE_POSITION_STATUSES}


def _in_flight_status_values() -> set[str]:
    return {status.value for status in _IN_FLIGHT_ORDER_STATUSES}


__all__ = [
    "OrderStateSource",
    "PositionStateSource",
    "build_runtime_projection",
]
