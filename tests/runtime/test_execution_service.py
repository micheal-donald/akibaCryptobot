from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone

import pytest

from domain.events import (
    EventMeta,
    FillPayload,
    OrderIntentPayload,
    OrderSubmittedPayload,
    OrderUpdatePayload,
    RuntimeEvent,
)
from services.execution_service import ExecutionService


class FakeExecutionGateway:
    def __init__(self) -> None:
        self.connected = 0
        self.disconnected = 0
        self.submissions: list[OrderIntentPayload] = []
        self.protection_calls: list[tuple[str, str, float, object]] = []
        self.next_response = OrderSubmittedPayload(
            client_order_id="intent-1",
            exchange_order_id="order-1",
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            requested_qty=0.5,
            requested_price=65000.0,
        )
        self.raise_on_submit: Exception | None = None
        self.raise_on_protection: Exception | None = None
        self.protection_response: tuple[OrderSubmittedPayload, ...] = (
            OrderSubmittedPayload(
                client_order_id="protect-1-sl",
                exchange_order_id="protect-order-sl",
                symbol="BTC/USDT",
                side="sell",
                order_type="stop_loss_limit",
                requested_qty=0.5,
                requested_price=63000.0,
                reduce_only=True,
            ),
            OrderSubmittedPayload(
                client_order_id="protect-1-tp",
                exchange_order_id="protect-order-tp",
                symbol="BTC/USDT",
                side="sell",
                order_type="take_profit_limit",
                requested_qty=0.5,
                requested_price=66000.0,
                reduce_only=True,
            ),
        )

    async def connect(self) -> None:
        self.connected += 1

    async def disconnect(self) -> None:
        self.disconnected += 1

    async def submit_order(self, intent: OrderIntentPayload) -> OrderSubmittedPayload:
        if self.raise_on_submit is not None:
            raise self.raise_on_submit
        self.submissions.append(intent)
        return self.next_response

    async def submit_protection(
        self,
        symbol: str,
        side: str,
        quantity: float,
        protection: object,
    ) -> tuple[OrderSubmittedPayload, ...]:
        if self.raise_on_protection is not None:
            raise self.raise_on_protection
        self.protection_calls.append((symbol, side, quantity, protection))
        return self.protection_response

    async def cancel_order(self, symbol: str, exchange_order_id: str) -> None:
        del symbol, exchange_order_id

    async def fetch_order(self, symbol: str, exchange_order_id: str) -> None:
        del symbol, exchange_order_id


class FakeUserStreamGateway:
    def __init__(self) -> None:
        self.connected = 0
        self.disconnected = 0
        self.subscribed = 0
        self.callbacks: dict[str, list[object]] = {
            "execution.order_update": [],
            "execution.fill": [],
            "execution.balance_update": [],
        }

    async def connect(self) -> None:
        self.connected += 1

    async def disconnect(self) -> None:
        self.disconnected += 1

    async def subscribe_user_stream(self) -> None:
        self.subscribed += 1

    def register_order_update_handler(self, handler: object) -> None:
        self.callbacks["execution.order_update"].append(handler)

    def register_fill_handler(self, handler: object) -> None:
        self.callbacks["execution.fill"].append(handler)

    def register_balance_update_handler(self, handler: object) -> None:
        self.callbacks["execution.balance_update"].append(handler)

    async def emit(self, event_type: str, event: RuntimeEvent) -> None:
        for callback in self.callbacks[event_type]:
            result = callback(event)
            if inspect.isawaitable(result):
                await result


def build_service() -> tuple[
    ExecutionService,
    FakeExecutionGateway,
    FakeUserStreamGateway,
    asyncio.Queue[RuntimeEvent],
]:
    execution_gateway = FakeExecutionGateway()
    user_stream_gateway = FakeUserStreamGateway()
    event_bus: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    service = ExecutionService(
        name="execution",
        execution_gateway=execution_gateway,
        user_stream_gateway=user_stream_gateway,
        event_bus=event_bus,
    )
    return service, execution_gateway, user_stream_gateway, event_bus


def test_execution_service_starts_and_registers_user_stream_handlers() -> None:
    service, execution_gateway, user_stream_gateway, _ = build_service()

    async def run() -> None:
        await service.start()
        await service.stop()

    asyncio.run(run())

    assert execution_gateway.connected == 1
    assert execution_gateway.disconnected == 1
    assert user_stream_gateway.connected == 1
    assert user_stream_gateway.subscribed == 1
    assert user_stream_gateway.disconnected == 1
    assert len(user_stream_gateway.callbacks["execution.order_update"]) == 1
    assert len(user_stream_gateway.callbacks["execution.fill"]) == 1
    assert len(user_stream_gateway.callbacks["execution.balance_update"]) == 1


def test_execution_service_submits_intents_idempotently_and_emits_event() -> None:
    service, execution_gateway, _, event_bus = build_service()
    intent = OrderIntentPayload(
        intent_id="intent-1",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=0.5,
        limit_price=65000.0,
        strategy="ensemble",
    )

    async def run() -> OrderSubmittedPayload:
        first = await service.submit_order_intent(intent)
        second = await service.submit_order_intent(intent)
        return first if first == second else second

    submitted = asyncio.run(run())

    assert execution_gateway.submissions == [intent]
    assert submitted.exchange_order_id == "order-1"
    assert service.get_order_state("intent-1") is not None
    assert service.get_order_state("intent-1").status == "accepted"
    assert event_bus.qsize() == 1
    event = event_bus.get_nowait()
    assert event.meta.event_type == "execution.order_submitted"
    assert isinstance(event.payload, OrderSubmittedPayload)


def test_execution_service_tracks_user_stream_updates() -> None:
    service, _, user_stream_gateway, event_bus = build_service()
    intent = OrderIntentPayload(
        intent_id="intent-1",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=0.5,
        stop_loss=63000.0,
        take_profit=66000.0,
    )
    order_event = RuntimeEvent(
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
    fill_event = RuntimeEvent(
        meta=EventMeta(
            event_id="evt-2",
            event_type="execution.fill",
            source="binance.ws.user.execution_report",
            symbol="BTC/USDT",
            ts_exchange=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
            ts_received=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
        ),
        payload=FillPayload(
            client_order_id="intent-1",
            exchange_order_id="order-1",
            symbol="BTC/USDT",
            side="buy",
            fill_qty=0.5,
            fill_price=65010.5,
            cumulative_qty=0.5,
            cumulative_quote_qty=32505.25,
            fee_cost=0.1,
            fee_currency="USDT",
            is_maker=False,
            reduce_only=False,
        ),
    )

    async def run() -> None:
        await service.start()
        await service.submit_order_intent(intent)
        await user_stream_gateway.emit("execution.order_update", order_event)
        await user_stream_gateway.emit("execution.fill", fill_event)
        await service.stop()

    asyncio.run(run())

    state = service.get_order_state("intent-1")
    assert state is not None
    assert state.order_id == "order-1"
    assert state.status == "filled"
    assert state.filled_qty == 0.5
    assert state.avg_fill_price == 65010.5
    assert service.get_protection_state("intent-1") is not None
    protection_state = service.get_protection_state("intent-1")
    assert protection_state is not None
    assert protection_state.status == "native_submitted"
    assert protection_state.mode == "native"
    assert protection_state.submitted_order_ids == (
        "protect-order-sl",
        "protect-order-tp",
    )
    assert event_bus.qsize() == 4
    assert [event_bus.get_nowait().meta.event_type for _ in range(4)] == [
        "execution.order_submitted",
        "execution.order_update",
        "execution.fill",
        "position.protective_submitted",
    ]


def test_execution_service_tracks_degraded_protection_state_on_failure() -> None:
    service, execution_gateway, user_stream_gateway, event_bus = build_service()
    execution_gateway.raise_on_protection = RuntimeError("native protection rejected")
    intent = OrderIntentPayload(
        intent_id="intent-2",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=0.5,
        stop_loss=63000.0,
        take_profit=66000.0,
    )
    fill_event = RuntimeEvent(
        meta=EventMeta(
            event_id="evt-2",
            event_type="execution.fill",
            source="binance.ws.user.execution_report",
            symbol="BTC/USDT",
            ts_exchange=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
            ts_received=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
        ),
        payload=FillPayload(
            client_order_id="intent-2",
            exchange_order_id="order-2",
            symbol="BTC/USDT",
            side="buy",
            fill_qty=0.5,
            fill_price=65010.5,
            cumulative_qty=0.5,
            cumulative_quote_qty=32505.25,
            fee_cost=0.1,
            fee_currency="USDT",
            is_maker=False,
            reduce_only=False,
        ),
    )

    async def run() -> None:
        await service.start()
        await service.submit_order_intent(intent)
        await user_stream_gateway.emit("execution.fill", fill_event)
        await service.stop()

    asyncio.run(run())

    protection_state = service.get_protection_state("intent-2")
    assert protection_state is not None
    assert protection_state.status == "degraded"
    assert protection_state.mode == "degraded"
    assert protection_state.reason == "native protection rejected"
    assert event_bus.qsize() == 3
    assert [event_bus.get_nowait().meta.event_type for _ in range(3)] == [
        "execution.order_submitted",
        "execution.fill",
        "position.protective_submitted",
    ]


def test_execution_service_emits_rejection_on_submit_failure() -> None:
    service, execution_gateway, _, event_bus = build_service()
    execution_gateway.raise_on_submit = RuntimeError("precision filter rejected")
    intent = OrderIntentPayload(
        intent_id="intent-1",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=0.5,
    )

    async def run() -> None:
        with pytest.raises(RuntimeError, match="precision filter rejected"):
            await service.submit_order_intent(intent)

    asyncio.run(run())

    state = service.get_order_state("intent-1")
    assert state is not None
    assert state.status == "rejected"
    assert event_bus.qsize() == 1
    event = event_bus.get_nowait()
    assert event.meta.event_type == "execution.order_rejected"
    assert event.payload["intent_id"] == "intent-1"


def test_execution_service_creates_order_state_from_fill_first_updates() -> None:
    service, _, user_stream_gateway, event_bus = build_service()
    fill_event = RuntimeEvent(
        meta=EventMeta(
            event_id="evt-3",
            event_type="execution.fill",
            source="binance.ws.user.execution_report",
            symbol="ETH/USDT",
            ts_exchange=datetime(2026, 3, 22, 9, 5, tzinfo=timezone.utc),
            ts_received=datetime(2026, 3, 22, 9, 5, tzinfo=timezone.utc),
        ),
        payload=FillPayload(
            client_order_id="intent-fill-first",
            exchange_order_id="order-fill-first",
            symbol="ETH/USDT",
            side="sell",
            fill_qty=0.4,
            fill_price=2200.5,
            cumulative_qty=0.4,
            cumulative_quote_qty=880.2,
            fee_cost=0.05,
            fee_currency="USDT",
            is_maker=True,
            reduce_only=True,
        ),
    )

    async def run() -> None:
        await service.start()
        await user_stream_gateway.emit("execution.fill", fill_event)
        await service.stop()

    asyncio.run(run())

    state = service.get_order_state("intent-fill-first")
    assert state is not None
    assert state.order_id == "order-fill-first"
    assert state.symbol == "ETH/USDT"
    assert state.side == "sell"
    assert state.status == "filled"
    assert state.filled_qty == 0.4
    assert state.avg_fill_price == 2200.5
    assert event_bus.qsize() == 1
    assert event_bus.get_nowait().meta.event_type == "execution.fill"
