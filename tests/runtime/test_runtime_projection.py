from __future__ import annotations

from datetime import datetime, timezone

from domain.state import OrderState, OrderStatus, PositionState, PositionStatus
from runtime.projection import build_runtime_projection
from runtime.view_models import ProjectionSource


class FakePositionSource:
    def __init__(self, *states: PositionState) -> None:
        self._states = states

    def get_all_position_states(self) -> tuple[PositionState, ...]:
        return tuple(self._states)


class FakeOrderSource:
    def __init__(self, *states: OrderState) -> None:
        self._states = states

    def get_all_order_states(self) -> tuple[OrderState, ...]:
        return tuple(self._states)


NOW = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)


def make_position(
    *,
    position_id: str,
    status: PositionStatus,
    open_qty: float,
    symbol: str = "BTC/USDT",
    protective_order_ids: tuple[str, ...] = (),
) -> PositionState:
    return PositionState(
        position_id=position_id,
        symbol=symbol,
        side="buy",
        requested_qty=max(open_qty, 1.0),
        status=status,
        open_qty=open_qty,
        avg_entry_price=65000.0,
        stop_loss=64000.0,
        take_profit=67000.0,
        entry_order_ids=("entry-1",),
        protective_order_ids=protective_order_ids,
        opened_at=NOW,
        updated_at=NOW,
    )


def make_order(
    *,
    client_order_id: str,
    status: OrderStatus,
    symbol: str = "BTC/USDT",
    reduce_only: bool = False,
) -> OrderState:
    return OrderState(
        client_order_id=client_order_id,
        symbol=symbol,
        side="sell" if reduce_only else "buy",
        order_type="market",
        requested_qty=1.0,
        status=status,
        order_id=f"ex-{client_order_id}",
        filled_qty=0.25 if status is OrderStatus.PARTIALLY_FILLED else 0.0,
        reduce_only=reduce_only,
        created_at=NOW,
        updated_at=NOW,
    )


def test_runtime_projection_empty_legacy_state() -> None:
    projection = build_runtime_projection(
        runtime_mode="legacy",
        runtime_is_running=False,
    )

    assert projection.source is ProjectionSource.LEGACY
    assert projection.runtime_mode == "legacy"
    assert projection.runtime_enabled is False
    assert projection.runtime_is_running is False
    assert projection.runtime_is_healthy is True
    assert projection.active_position_count == 0
    assert projection.protected_position_count == 0
    assert projection.pending_exit_count == 0
    assert projection.open_order_count == 0
    assert projection.rejected_order_count == 0
    assert projection.positions == ()
    assert projection.orders == ()


def test_runtime_projection_summarizes_open_and_protected_positions() -> None:
    projection = build_runtime_projection(
        runtime_mode="runtime_primary",
        runtime_is_running=True,
        position_source=FakePositionSource(
            make_position(
                position_id="pos-open", status=PositionStatus.OPEN, open_qty=1.0
            ),
            make_position(
                position_id="pos-protected",
                status=PositionStatus.PROTECTED,
                open_qty=0.5,
                protective_order_ids=("sl-1", "tp-1"),
                symbol="ETH/USDT",
            ),
        ),
    )

    assert projection.source is ProjectionSource.RUNTIME_PROJECTION
    assert projection.runtime_owns_entries is True
    assert projection.runtime_owns_exits is True
    assert projection.active_position_count == 2
    assert projection.protected_position_count == 1
    assert projection.pending_exit_count == 0
    assert tuple(position.symbol for position in projection.positions) == (
        "BTC/USDT",
        "ETH/USDT",
    )
    assert projection.positions[1].protective_order_ids == ("sl-1", "tp-1")


def test_runtime_projection_counts_pending_exit_positions() -> None:
    projection = build_runtime_projection(
        runtime_mode="runtime_primary",
        runtime_is_running=True,
        position_source=FakePositionSource(
            make_position(
                position_id="pos-reduce",
                status=PositionStatus.REDUCE_PENDING,
                open_qty=0.25,
            )
        ),
    )

    assert projection.active_position_count == 1
    assert projection.pending_exit_count == 1
    assert projection.positions[0].status == PositionStatus.REDUCE_PENDING.value


def test_runtime_projection_summarizes_in_flight_and_rejected_orders() -> None:
    projection = build_runtime_projection(
        runtime_mode="runtime_entries",
        runtime_is_running=True,
        execution_source=FakeOrderSource(
            make_order(client_order_id="ord-accepted", status=OrderStatus.ACCEPTED),
            make_order(
                client_order_id="ord-rejected",
                status=OrderStatus.REJECTED,
                symbol="ETH/USDT",
            ),
        ),
    )

    assert projection.source is ProjectionSource.RUNTIME_PROJECTION
    assert projection.runtime_owns_entries is True
    assert projection.runtime_owns_exits is False
    assert projection.open_order_count == 1
    assert projection.rejected_order_count == 1
    assert tuple(order.status for order in projection.orders) == (
        OrderStatus.ACCEPTED.value,
        OrderStatus.REJECTED.value,
    )


def test_runtime_projection_marks_shadow_mode_source() -> None:
    projection = build_runtime_projection(
        runtime_mode="shadow",
        runtime_is_running=True,
    )

    assert projection.source is ProjectionSource.SHADOW
    assert projection.runtime_enabled is True
    assert projection.runtime_is_healthy is True
    assert projection.runtime_owns_entries is False
    assert projection.runtime_owns_exits is False


def test_runtime_projection_marks_runtime_startup_pending() -> None:
    projection = build_runtime_projection(
        runtime_mode="shadow",
        runtime_is_running=False,
        runtime_start_status="starting",
    )

    assert projection.source is ProjectionSource.SHADOW
    assert projection.runtime_is_running is False
    assert projection.runtime_is_healthy is False
    assert projection.runtime_start_status == "starting"
    assert projection.runtime_start_pending is True
