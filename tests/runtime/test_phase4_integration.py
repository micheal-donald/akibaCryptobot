from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from adapters.sqlite.runtime_store import SQLiteRuntimeStore
from domain.events import (
    OrderIntentPayload,
    OrderSubmittedPayload,
    OrderUpdatePayload,
    RuntimeEvent,
)
from runtime.app import RuntimeApp
from runtime.config import RuntimeConfig
from services.recovery_service import RestartRecoveryService


@dataclass
class MemoryRuntimeStore:
    events: list[RuntimeEvent]
    reports: list[object]
    initialize_calls: int = 0
    close_calls: int = 0

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def record_event(self, event: RuntimeEvent) -> None:
        self.events.append(event)

    async def record_recovery_report(self, report: object) -> None:
        self.reports.append(report)


@dataclass
class LaggingRuntimeStore:
    events: list[RuntimeEvent]
    reports: list[object]
    delay_seconds: float = 0.25
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


@dataclass
class FailingRuntimeStore:
    events: list[RuntimeEvent]
    reports: list[object]
    event_failures: int = 0
    report_failures: int = 0
    initialize_calls: int = 0
    close_calls: int = 0

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def record_event(self, event: RuntimeEvent) -> None:
        del event
        self.event_failures += 1
        raise RuntimeError("event write failed")

    async def record_recovery_report(self, report: object) -> None:
        del report
        self.report_failures += 1
        raise RuntimeError("recovery report write failed")


class FakeExecutionGateway:
    def __init__(self) -> None:
        self.connected = 0
        self.disconnected = 0
        self.submissions: list[OrderIntentPayload] = []
        self.snapshots: dict[str, OrderUpdatePayload] = {}
        self.next_response = OrderSubmittedPayload(
            client_order_id="intent-1",
            exchange_order_id="order-1",
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            requested_qty=0.5,
            requested_price=65000.0,
        )

    async def connect(self) -> None:
        self.connected += 1

    async def disconnect(self) -> None:
        self.disconnected += 1

    async def submit_order(self, intent: OrderIntentPayload) -> OrderSubmittedPayload:
        self.submissions.append(intent)
        self.next_response = OrderSubmittedPayload(
            client_order_id=intent.intent_id,
            exchange_order_id="order-1",
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            requested_qty=intent.quantity,
            requested_price=intent.limit_price,
            reduce_only=intent.reduce_only,
        )
        return self.next_response

    async def submit_protection(
        self,
        symbol: str,
        side: str,
        quantity: float,
        protection: object,
    ) -> tuple[object, ...]:
        del symbol, side, quantity, protection
        return ()

    async def cancel_order(self, symbol: str, exchange_order_id: str) -> None:
        del symbol, exchange_order_id

    async def fetch_order(
        self, symbol: str, exchange_order_id: str
    ) -> OrderUpdatePayload:
        del symbol
        return self.snapshots[exchange_order_id]


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


class FakeTradeStore:
    def __init__(self, open_trades: dict[str, dict]) -> None:
        self.open_trades = open_trades
        self.calls = 0

    async def load_open_trades(self) -> dict[str, dict]:
        self.calls += 1
        return self.open_trades


def make_config(*, queue_maxsize: int = 4) -> RuntimeConfig:
    return RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        queue_maxsize=queue_maxsize,
        shadow_mode=False,
    )


def make_allowed_intent(*, intent_id: str = "intent-1") -> OrderIntentPayload:
    return OrderIntentPayload(
        intent_id=intent_id,
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=0.5,
        stop_loss=63000.0,
        take_profit=66000.0,
        strategy="ensemble",
    )


def make_blocked_intent(*, intent_id: str = "intent-blocked") -> OrderIntentPayload:
    return OrderIntentPayload(
        intent_id=intent_id,
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=0.0,
    )


def make_recovery_snapshot(*, client_order_id: str = "order-1") -> OrderUpdatePayload:
    return OrderUpdatePayload(
        client_order_id=client_order_id,
        exchange_order_id=client_order_id,
        symbol="BTC/USDT",
        status="cancelled",
        filled_qty=0.0,
        remaining_qty=0.5,
        avg_fill_price=None,
        reduce_only=False,
        raw_status="CANCELED",
    )


def make_open_trade(*, order_id: str = "order-1") -> dict:
    return {
        "db_id": 1,
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": 0.5,
        "entry_price": 65000.0,
        "stop_loss": 63000.0,
        "take_profit": 66000.0,
        "timestamp": datetime.now(timezone.utc),
        "order_id": order_id,
        "status": "open",
        "fees": 0.1,
        "trailing_stop_price": None,
    }


def test_phase4_blocks_intents_and_persists_risk_events() -> None:
    execution_gateway = FakeExecutionGateway()
    user_stream_gateway = FakeUserStreamGateway()
    store = MemoryRuntimeStore(events=[], reports=[])
    app = RuntimeApp.build_phase4(
        make_config(),
        execution_gateway=execution_gateway,
        runtime_store=store,
        user_stream_gateway=user_stream_gateway,
    )
    blocked_intent = make_blocked_intent()

    async def run() -> None:
        await app.start()
        submitted = await app.submit_order_intent(blocked_intent)
        assert submitted is None
        assert execution_gateway.submissions == []
        assert await app.persistence_service.wait_until_drained(timeout_seconds=1.0)
        assert app.telemetry_service is not None
        assert app.telemetry_service.get_event_count("risk.blocked") == 1
        await app.stop()

    asyncio.run(run())

    assert [event.meta.event_type for event in store.events] == ["risk.blocked"]
    assert store.reports == []


def test_phase4_allows_intents_and_persists_execution_events() -> None:
    execution_gateway = FakeExecutionGateway()
    user_stream_gateway = FakeUserStreamGateway()
    store = MemoryRuntimeStore(events=[], reports=[])
    app = RuntimeApp.build_phase4(
        make_config(),
        execution_gateway=execution_gateway,
        runtime_store=store,
        user_stream_gateway=user_stream_gateway,
    )
    allowed_intent = make_allowed_intent()

    async def run() -> None:
        await app.start()
        submitted = await app.submit_order_intent(allowed_intent)
        assert submitted is not None
        assert execution_gateway.submissions == [allowed_intent]
        assert await app.persistence_service.wait_until_drained(timeout_seconds=1.0)
        assert app.telemetry_service is not None
        assert app.telemetry_service.get_event_count("execution.order_submitted") == 1
        await app.stop()

    asyncio.run(run())

    assert [event.meta.event_type for event in store.events] == [
        "execution.order_submitted"
    ]
    assert store.reports == []


def test_phase4_persistence_lag_keeps_hot_path_unblocked() -> None:
    execution_gateway = FakeExecutionGateway()
    user_stream_gateway = FakeUserStreamGateway()
    store = LaggingRuntimeStore(events=[], reports=[], delay_seconds=0.25)
    app = RuntimeApp.build_phase4(
        make_config(),
        execution_gateway=execution_gateway,
        runtime_store=store,
        user_stream_gateway=user_stream_gateway,
    )
    allowed_intent = make_allowed_intent(intent_id="intent-lag")

    async def run() -> None:
        await app.start()
        submitted = await app.submit_order_intent(allowed_intent)
        assert submitted is not None
        assert execution_gateway.submissions == [allowed_intent]
        assert store.events == []
        assert await app.persistence_service.wait_until_drained(timeout_seconds=2.0)
        await app.stop()

    asyncio.run(run())

    assert [event.meta.event_type for event in store.events] == [
        "execution.order_submitted"
    ]


def test_phase4_persistence_failure_is_recorded_without_blocking_execution() -> None:
    execution_gateway = FakeExecutionGateway()
    user_stream_gateway = FakeUserStreamGateway()
    store = FailingRuntimeStore(events=[], reports=[])
    app = RuntimeApp.build_phase4(
        make_config(),
        execution_gateway=execution_gateway,
        runtime_store=store,
        user_stream_gateway=user_stream_gateway,
    )
    allowed_intent = make_allowed_intent(intent_id="intent-fail")

    async def run() -> None:
        await app.start()
        submitted = await app.submit_order_intent(allowed_intent)
        assert submitted is not None
        assert execution_gateway.submissions == [allowed_intent]
        assert await app.persistence_service.wait_until_drained(timeout_seconds=1.0)
        await app.stop()

    asyncio.run(run())

    assert store.event_failures == 1
    assert app.persistence_service is not None
    assert app.persistence_service.get_failure_counts()["failed_writes"] == 1
    assert app.persistence_service.get_persisted_counts()["events"] == 0


def test_phase4_recovery_issues_materialize_into_persistent_reports(
    tmp_path,
) -> None:
    db_path = tmp_path / "runtime.db"
    runtime_store = SQLiteRuntimeStore(db_path=str(db_path))
    execution_gateway = FakeExecutionGateway()
    execution_gateway.snapshots = {"order-1": make_recovery_snapshot()}
    user_stream_gateway = FakeUserStreamGateway()
    app = RuntimeApp.build_phase4(
        make_config(),
        execution_gateway=execution_gateway,
        runtime_store=runtime_store,
        user_stream_gateway=user_stream_gateway,
    )
    trade_store = FakeTradeStore({"BTC/USDT": make_open_trade()})
    recovery_service = RestartRecoveryService(
        name="recovery",
        trade_store=trade_store,
        execution_gateway=execution_gateway,
        execution_service=app.execution_service,
        position_service=app.position_service,
        max_recovery_items=10,
        fetch_timeout_seconds=1.0,
        reapply_native_protection=False,
    )

    async def run() -> None:
        await app.start()
        await recovery_service.start()
        report = await recovery_service.recover()
        assert report.orphaned_symbols == ("BTC/USDT",)
        assert report.issues[0].kind == "orphaned_order"
        assert app.persistence_service is not None
        assert app.persistence_service.enqueue_recovery_report(report)
        assert await app.persistence_service.wait_until_drained(timeout_seconds=1.0)
        rows = await runtime_store.fetch_recovery_reports()
        assert len(rows) == 1
        issues = json.loads(rows[0]["issues_json"])
        assert issues[0]["symbol"] == "BTC/USDT"
        assert issues[0]["kind"] == "orphaned_order"
        await recovery_service.stop()
        await app.stop()

    asyncio.run(run())

    assert trade_store.calls == 1
