from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from domain.events import EventMeta, FillPayload, OrderUpdatePayload, RuntimeEvent
from domain.state import PositionStatus
from services.position_service import PositionService


def build_service() -> tuple[PositionService, asyncio.Queue[RuntimeEvent]]:
    event_bus: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    service = PositionService(name="position", event_bus=event_bus)
    return service, event_bus


def test_position_service_tracks_entry_orders_from_updates() -> None:
    service, event_bus = build_service()
    event = RuntimeEvent(
        meta=EventMeta(
            event_id="evt-1",
            event_type="execution.order_update",
            source="binance.ws.user.execution_report",
            symbol="BTC/USDT",
            ts_exchange=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
            ts_received=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
        ),
        payload=OrderUpdatePayload(
            client_order_id="intent-1",
            exchange_order_id="order-1",
            symbol="BTC/USDT",
            status="partially_filled",
            filled_qty=0.2,
            remaining_qty=0.3,
            avg_fill_price=65010.5,
            reduce_only=False,
        ),
    )

    asyncio.run(service.handle_order_update_event(event))

    state = service.get_position_for_symbol("BTC/USDT")
    assert state is not None
    assert state.position_id == "intent-1"
    assert state.status == PositionStatus.ENTRY_PENDING
    assert state.requested_qty == 0.5
    assert state.open_qty == 0.2
    assert state.avg_entry_price == 65010.5
    assert state.entry_order_ids == ("order-1",)
    assert event_bus.qsize() == 1
    assert event_bus.get_nowait().meta.event_type == "position.opened"


def test_position_service_opens_from_fill_and_emits_open_event() -> None:
    service, event_bus = build_service()
    event = RuntimeEvent(
        meta=EventMeta(
            event_id="evt-2",
            event_type="execution.fill",
            source="binance.ws.user.execution_report",
            symbol="ETH/USDT",
            ts_exchange=datetime(2026, 3, 22, 9, 1, tzinfo=timezone.utc),
            ts_received=datetime(2026, 3, 22, 9, 1, tzinfo=timezone.utc),
        ),
        payload=FillPayload(
            client_order_id="intent-2",
            exchange_order_id="order-2",
            symbol="ETH/USDT",
            side="sell",
            fill_qty=0.4,
            fill_price=2200.5,
            cumulative_qty=0.4,
            cumulative_quote_qty=880.2,
            fee_cost=0.05,
            fee_currency="USDT",
            is_maker=True,
            reduce_only=False,
        ),
    )

    asyncio.run(service.handle_fill_event(event))

    state = service.get_position_for_symbol("ETH/USDT")
    assert state is not None
    assert state.status == PositionStatus.OPEN
    assert state.open_qty == 0.4
    assert state.avg_entry_price == 2200.5
    assert state.entry_order_ids == ("order-2",)
    assert event_bus.qsize() == 1
    emitted = event_bus.get_nowait()
    assert emitted.meta.event_type == "position.opened"
    assert emitted.payload.position_id == "intent-2"
    assert emitted.payload.quantity == 0.4


def test_position_service_marks_and_detects_protective_orders() -> None:
    service, _ = build_service()
    entry_event = RuntimeEvent(
        meta=EventMeta(
            event_id="evt-3",
            event_type="execution.fill",
            source="binance.ws.user.execution_report",
            symbol="BTC/USDT",
            ts_exchange=datetime(2026, 3, 22, 9, 2, tzinfo=timezone.utc),
            ts_received=datetime(2026, 3, 22, 9, 2, tzinfo=timezone.utc),
        ),
        payload=FillPayload(
            client_order_id="intent-3",
            exchange_order_id="order-3",
            symbol="BTC/USDT",
            side="buy",
            fill_qty=0.25,
            fill_price=64000.0,
            cumulative_qty=0.25,
            cumulative_quote_qty=16000.0,
            fee_cost=0.02,
            fee_currency="USDT",
            is_maker=False,
            reduce_only=False,
        ),
    )

    asyncio.run(service.handle_fill_event(entry_event))

    unprotected = service.get_unprotected_positions()
    assert len(unprotected) == 1
    assert unprotected[0].position_id == "intent-3"

    state = service.mark_protective_orders(
        "intent-3",
        ("stop-1", "take-1"),
        stop_loss=63000.0,
        take_profit=66000.0,
    )

    assert state.status == PositionStatus.PROTECTED
    assert state.stop_loss == 63000.0
    assert state.take_profit == 66000.0
    assert state.protective_order_ids == ("stop-1", "take-1")
    assert service.get_unprotected_positions() == ()


def test_position_service_reports_protective_pending_positions() -> None:
    service, _ = build_service()
    entry_event = RuntimeEvent(
        meta=EventMeta(
            event_id="evt-3b",
            event_type="execution.fill",
            source="binance.ws.user.execution_report",
            symbol="ETH/USDT",
            ts_exchange=datetime(2026, 3, 22, 9, 2, tzinfo=timezone.utc),
            ts_received=datetime(2026, 3, 22, 9, 2, tzinfo=timezone.utc),
        ),
        payload=FillPayload(
            client_order_id="intent-3b",
            exchange_order_id="order-3b",
            symbol="ETH/USDT",
            side="buy",
            fill_qty=0.3,
            fill_price=2300.0,
            cumulative_qty=0.3,
            cumulative_quote_qty=690.0,
            fee_cost=0.01,
            fee_currency="USDT",
            is_maker=False,
            reduce_only=False,
        ),
    )

    asyncio.run(service.handle_fill_event(entry_event))
    state = service.mark_protective_orders("intent-3b", ())

    assert state.status == PositionStatus.PROTECTIVE_PENDING
    unprotected = service.get_unprotected_positions()
    assert len(unprotected) == 1
    assert unprotected[0].position_id == "intent-3b"


def test_position_service_closes_from_reduce_only_fill() -> None:
    service, event_bus = build_service()
    entry_event = RuntimeEvent(
        meta=EventMeta(
            event_id="evt-4",
            event_type="execution.fill",
            source="binance.ws.user.execution_report",
            symbol="BTC/USDT",
            ts_exchange=datetime(2026, 3, 22, 9, 3, tzinfo=timezone.utc),
            ts_received=datetime(2026, 3, 22, 9, 3, tzinfo=timezone.utc),
        ),
        payload=FillPayload(
            client_order_id="intent-4",
            exchange_order_id="order-4",
            symbol="BTC/USDT",
            side="buy",
            fill_qty=0.5,
            fill_price=65000.0,
            cumulative_qty=0.5,
            cumulative_quote_qty=32500.0,
            fee_cost=0.04,
            fee_currency="USDT",
            is_maker=False,
            reduce_only=False,
        ),
    )
    exit_event = RuntimeEvent(
        meta=EventMeta(
            event_id="evt-5",
            event_type="execution.fill",
            source="binance.ws.user.execution_report",
            symbol="BTC/USDT",
            ts_exchange=datetime(2026, 3, 22, 9, 4, tzinfo=timezone.utc),
            ts_received=datetime(2026, 3, 22, 9, 4, tzinfo=timezone.utc),
        ),
        payload=FillPayload(
            client_order_id="intent-4",
            exchange_order_id="close-4",
            symbol="BTC/USDT",
            side="sell",
            fill_qty=0.5,
            fill_price=65100.0,
            cumulative_qty=0.5,
            cumulative_quote_qty=32550.0,
            fee_cost=0.03,
            fee_currency="USDT",
            is_maker=True,
            reduce_only=True,
        ),
    )

    asyncio.run(service.handle_fill_event(entry_event))
    asyncio.run(service.handle_fill_event(exit_event))

    state = service.get_position_state("intent-4")
    assert state is not None
    assert state.status == PositionStatus.CLOSED
    assert state.open_qty == 0.0
    assert state.avg_exit_price == 65100.0
    assert state.closed_at is not None
    assert state.side == "buy"
    assert event_bus.qsize() == 2
    assert [event_bus.get_nowait().meta.event_type for _ in range(2)] == [
        "position.opened",
        "position.closed",
    ]


def test_position_service_aggregates_partial_reduce_only_exit_fills() -> None:
    service, event_bus = build_service()
    entry_event = RuntimeEvent(
        meta=EventMeta(
            event_id="evt-6",
            event_type="execution.fill",
            source="binance.ws.user.execution_report",
            symbol="BTC/USDT",
            ts_exchange=datetime(2026, 3, 22, 9, 5, tzinfo=timezone.utc),
            ts_received=datetime(2026, 3, 22, 9, 5, tzinfo=timezone.utc),
        ),
        payload=FillPayload(
            client_order_id="intent-6",
            exchange_order_id="order-6",
            symbol="BTC/USDT",
            side="buy",
            fill_qty=0.5,
            fill_price=65000.0,
            cumulative_qty=0.5,
            cumulative_quote_qty=32500.0,
            fee_cost=0.02,
            fee_currency="USDT",
            is_maker=False,
            reduce_only=False,
        ),
    )
    first_exit_event = RuntimeEvent(
        meta=EventMeta(
            event_id="evt-7",
            event_type="execution.fill",
            source="binance.ws.user.execution_report",
            symbol="BTC/USDT",
            ts_exchange=datetime(2026, 3, 22, 9, 6, tzinfo=timezone.utc),
            ts_received=datetime(2026, 3, 22, 9, 6, tzinfo=timezone.utc),
        ),
        payload=FillPayload(
            client_order_id="intent-6",
            exchange_order_id="close-6",
            symbol="BTC/USDT",
            side="sell",
            fill_qty=0.2,
            fill_price=65100.0,
            cumulative_qty=0.2,
            cumulative_quote_qty=13020.0,
            fee_cost=0.01,
            fee_currency="USDT",
            is_maker=False,
            reduce_only=True,
        ),
    )
    second_exit_event = RuntimeEvent(
        meta=EventMeta(
            event_id="evt-8",
            event_type="execution.fill",
            source="binance.ws.user.execution_report",
            symbol="BTC/USDT",
            ts_exchange=datetime(2026, 3, 22, 9, 7, tzinfo=timezone.utc),
            ts_received=datetime(2026, 3, 22, 9, 7, tzinfo=timezone.utc),
        ),
        payload=FillPayload(
            client_order_id="intent-6",
            exchange_order_id="close-6",
            symbol="BTC/USDT",
            side="sell",
            fill_qty=0.3,
            fill_price=65200.0,
            cumulative_qty=0.5,
            cumulative_quote_qty=32580.0,
            fee_cost=0.01,
            fee_currency="USDT",
            is_maker=True,
            reduce_only=True,
        ),
    )

    asyncio.run(service.handle_fill_event(entry_event))
    asyncio.run(service.handle_fill_event(first_exit_event))
    interim_state = service.get_position_state("intent-6")
    assert interim_state is not None
    assert interim_state.status == PositionStatus.REDUCE_PENDING
    assert interim_state.open_qty == pytest.approx(0.3)
    assert interim_state.avg_exit_price == pytest.approx(65100.0)

    asyncio.run(service.handle_fill_event(second_exit_event))

    final_state = service.get_position_state("intent-6")
    assert final_state is not None
    assert final_state.status == PositionStatus.CLOSED
    assert final_state.open_qty == 0.0
    assert final_state.avg_exit_price == pytest.approx(65160.0)
    assert event_bus.qsize() == 2
