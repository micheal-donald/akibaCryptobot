from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone

from domain.events import (
    EventMeta,
    FillPayload,
    OrderIntentPayload,
    OrderSubmittedPayload,
    OrderUpdatePayload,
    RuntimeEvent,
)
from domain.state import PositionStatus
from runtime.app import RuntimeApp
from runtime.config import RuntimeConfig


@dataclass
class SlowRuntimeStore:
    events: list[RuntimeEvent]
    reports: list[object]
    delay_seconds: float = 0.05
    initialize_calls: int = 0
    close_calls: int = 0

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def record_event(self, event: RuntimeEvent) -> None:
        await asyncio.sleep(self.delay_seconds)
        self.events.append(event)

    async def record_recovery_report(self, report: object) -> None:
        await asyncio.sleep(self.delay_seconds)
        self.reports.append(report)


class FakeExecutionGateway:
    def __init__(self) -> None:
        self.connected = 0
        self.disconnected = 0
        self.submissions: list[OrderIntentPayload] = []
        self.protection_calls: list[tuple[str, str, float, object]] = []
        self.next_order_response = OrderSubmittedPayload(
            client_order_id="intent-1",
            exchange_order_id="order-1",
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            requested_qty=0.5,
            requested_price=65000.0,
        )
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
        self.submissions.append(intent)
        self.next_order_response = OrderSubmittedPayload(
            client_order_id=intent.intent_id,
            exchange_order_id="order-1",
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            requested_qty=intent.quantity,
            requested_price=intent.limit_price,
            reduce_only=intent.reduce_only,
        )
        return self.next_order_response

    async def submit_protection(
        self,
        symbol: str,
        side: str,
        quantity: float,
        protection: object,
    ) -> tuple[OrderSubmittedPayload, ...]:
        self.protection_calls.append((symbol, side, quantity, protection))
        return self.protection_response

    async def cancel_order(self, symbol: str, exchange_order_id: str):
        del symbol, exchange_order_id

    async def fetch_order(self, symbol: str, exchange_order_id: str):
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


def make_config() -> RuntimeConfig:
    return RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        queue_maxsize=4,
        shadow_mode=False,
    )


def test_phase4_runtime_shell_applies_persistence_batch_tuning_from_config() -> None:
    execution_gateway = FakeExecutionGateway()
    user_stream_gateway = FakeUserStreamGateway()
    store = SlowRuntimeStore(events=[], reports=[], delay_seconds=0.01)
    app = RuntimeApp.build_phase4(
        RuntimeConfig(
            symbols=("BTC/USDT",),
            kline_intervals=("1m",),
            queue_maxsize=4,
            max_batch_size=32,
            flush_interval_seconds=0.4,
            shadow_mode=False,
        ),
        execution_gateway=execution_gateway,
        runtime_store=store,
        user_stream_gateway=user_stream_gateway,
    )

    assert app.persistence_service is not None
    assert app.persistence_service.max_batch_size == 32
    assert app.persistence_service.flush_interval_seconds == 0.4


def make_order_intent() -> OrderIntentPayload:
    return OrderIntentPayload(
        intent_id="intent-1",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=0.5,
        stop_loss=63000.0,
        take_profit=66000.0,
        strategy="ensemble",
    )


def make_fill_event() -> RuntimeEvent:
    ts = datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc)
    return RuntimeEvent(
        meta=EventMeta(
            event_id="evt-fill",
            event_type="execution.fill",
            source="binance.ws.user.execution_report",
            symbol="BTC/USDT",
            ts_exchange=ts,
            ts_received=ts,
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


def make_order_update_event() -> RuntimeEvent:
    ts = datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc)
    return RuntimeEvent(
        meta=EventMeta(
            event_id="evt-order-update",
            event_type="execution.order_update",
            source="binance.ws.user.execution_report",
            symbol="BTC/USDT",
            ts_exchange=ts,
            ts_received=ts,
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


def test_phase4_runtime_shell_blocks_intents_before_execution() -> None:
    execution_gateway = FakeExecutionGateway()
    user_stream_gateway = FakeUserStreamGateway()
    store = SlowRuntimeStore(events=[], reports=[], delay_seconds=0.05)
    app = RuntimeApp.build_phase4(
        make_config(),
        execution_gateway=execution_gateway,
        runtime_store=store,
        user_stream_gateway=user_stream_gateway,
    )
    blocked_intent = OrderIntentPayload(
        intent_id="intent-blocked",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=0.0,
    )

    async def run() -> None:
        await app.start()
        submitted = await app.submit_order_intent(blocked_intent)
        assert submitted is None
        assert execution_gateway.submissions == []
        assert await app.persistence_service.wait_until_drained(timeout_seconds=1.0)
        await app.stop()

    asyncio.run(run())

    assert store.initialize_calls == 1
    assert store.close_calls == 1
    assert [event.meta.event_type for event in store.events] == ["risk.blocked"]
    assert app.telemetry_service is not None
    assert app.telemetry_service.get_event_count("risk.blocked") == 1


def test_phase4_runtime_shell_routes_allowed_intents_and_persists_later() -> None:
    execution_gateway = FakeExecutionGateway()
    user_stream_gateway = FakeUserStreamGateway()
    store = SlowRuntimeStore(events=[], reports=[], delay_seconds=0.1)
    app = RuntimeApp.build_phase4(
        make_config(),
        execution_gateway=execution_gateway,
        runtime_store=store,
        user_stream_gateway=user_stream_gateway,
    )
    intent = make_order_intent()

    async def run() -> None:
        await app.start()
        submitted = await app.submit_order_intent(intent)
        assert submitted is not None
        assert execution_gateway.submissions == [intent]
        assert store.events == []

        await user_stream_gateway.emit(
            "execution.order_update", make_order_update_event()
        )
        await user_stream_gateway.emit("execution.fill", make_fill_event())
        assert await app.persistence_service.wait_until_drained(timeout_seconds=2.0)
        await app.stop()

    asyncio.run(run())

    event_types = [event.meta.event_type for event in store.events]
    assert event_types[0] == "execution.order_submitted"
    assert "execution.order_update" in event_types
    assert "execution.fill" in event_types
    assert "position.protective_submitted" in event_types

    position = app.position_service.get_position_for_symbol("BTC/USDT")
    assert position is not None
    assert position.status == PositionStatus.PROTECTED
    assert position.protective_order_ids == (
        "protect-order-sl",
        "protect-order-tp",
    )


def test_phase4_runtime_shell_owns_runtime_store_lifecycle() -> None:
    execution_gateway = FakeExecutionGateway()
    user_stream_gateway = FakeUserStreamGateway()
    store = SlowRuntimeStore(events=[], reports=[])
    app = RuntimeApp.build_phase4(
        make_config(),
        execution_gateway=execution_gateway,
        runtime_store=store,
        user_stream_gateway=user_stream_gateway,
    )

    async def run() -> None:
        await app.start()
        await app.stop()

    asyncio.run(run())

    assert store.initialize_calls == 1
    assert store.close_calls == 1
    assert app.is_running is False
