from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone

from domain.events import (
    EventMeta,
    FillPayload,
    OrderIntentPayload,
    OrderSubmittedPayload,
    OrderUpdatePayload,
    RuntimeEvent,
)
from services.execution_service import ExecutionService
from services.position_service import PositionService
from services.recovery_service import RestartRecoveryService


class FakeExecutionGateway:
    def __init__(self) -> None:
        self.connected = 0
        self.disconnected = 0
        self.submissions: list[OrderIntentPayload] = []
        self.protection_calls: list[tuple[str, str, float, object]] = []
        self.order_response = OrderSubmittedPayload(
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
        self.snapshots: dict[str, OrderUpdatePayload] = {}

    async def connect(self) -> None:
        self.connected += 1

    async def disconnect(self) -> None:
        self.disconnected += 1

    async def submit_order(self, intent: OrderIntentPayload) -> OrderSubmittedPayload:
        self.submissions.append(intent)
        self.order_response = OrderSubmittedPayload(
            client_order_id=intent.intent_id,
            exchange_order_id="order-1",
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            requested_qty=intent.quantity,
            requested_price=intent.limit_price,
            reduce_only=intent.reduce_only,
        )
        return self.order_response

    async def submit_protection(
        self,
        symbol: str,
        side: str,
        quantity: float,
        protection: object,
    ) -> tuple[OrderSubmittedPayload, ...]:
        self.protection_calls.append((symbol, side, quantity, protection))
        return self.protection_response

    async def cancel_order(self, symbol: str, exchange_order_id: str) -> None:
        del symbol, exchange_order_id

    async def fetch_order(
        self, symbol: str, exchange_order_id: str
    ) -> OrderUpdatePayload:
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

    async def emit(self, event_type: str, event: RuntimeEvent) -> None:
        for callback in self.callbacks[event_type]:
            result = callback(event)
            if inspect.isawaitable(result):
                await result


class FakeTradeStore:
    def __init__(self, open_trades: dict[str, dict]) -> None:
        self.open_trades = open_trades
        self.calls = 0

    async def load_open_trades(self) -> dict[str, dict]:
        self.calls += 1
        return self.open_trades


async def _drain_runtime_events(
    event_bus: asyncio.Queue[RuntimeEvent],
    position_service: PositionService,
) -> list[str]:
    seen: list[str] = []
    while not event_bus.empty():
        event = await event_bus.get()
        seen.append(event.meta.event_type)
        if event.meta.event_type in {"execution.order_update", "execution.fill"}:
            await position_service.handle_execution_event(event)
        elif event.meta.event_type == "position.protective_submitted":
            payload = event.payload
            if (
                isinstance(payload, dict)
                and payload.get("status") == "native_submitted"
            ):
                position_service.mark_protective_orders(
                    str(payload["client_order_id"]),
                    tuple(str(order_id) for order_id in payload["submitted_order_ids"]),
                    stop_loss=payload.get("stop_loss"),
                    take_profit=payload.get("take_profit"),
                    trailing_stop=payload.get("trailing_stop"),
                )
    return seen


def test_phase3_live_lifecycle_entry_fill_protection_and_close() -> None:
    event_bus: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    execution_gateway = FakeExecutionGateway()
    user_stream_gateway = FakeUserStreamGateway()
    execution_service = ExecutionService(
        name="execution",
        execution_gateway=execution_gateway,
        user_stream_gateway=user_stream_gateway,
        event_bus=event_bus,
    )
    position_service = PositionService(name="position", event_bus=event_bus)
    intent = OrderIntentPayload(
        intent_id="intent-1",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=0.5,
        stop_loss=63000.0,
        take_profit=66000.0,
        strategy="ensemble",
    )

    async def run() -> list[str]:
        seen_event_types: list[str] = []
        await execution_service.start()
        await position_service.start()
        await execution_service.submit_order_intent(intent)
        seen_event_types.extend(
            await _drain_runtime_events(event_bus, position_service)
        )

        await user_stream_gateway.emit(
            "execution.order_update",
            RuntimeEvent(
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
            ),
        )
        seen_event_types.extend(
            await _drain_runtime_events(event_bus, position_service)
        )

        await user_stream_gateway.emit(
            "execution.fill",
            RuntimeEvent(
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
            ),
        )
        seen_event_types.extend(
            await _drain_runtime_events(event_bus, position_service)
        )

        await user_stream_gateway.emit(
            "execution.fill",
            RuntimeEvent(
                meta=EventMeta(
                    event_id="evt-3",
                    event_type="execution.fill",
                    source="binance.ws.user.execution_report",
                    symbol="BTC/USDT",
                    ts_exchange=datetime(2026, 3, 22, 9, 1, tzinfo=timezone.utc),
                    ts_received=datetime(2026, 3, 22, 9, 1, tzinfo=timezone.utc),
                ),
                payload=FillPayload(
                    client_order_id="intent-1",
                    exchange_order_id="protect-order-sl",
                    symbol="BTC/USDT",
                    side="sell",
                    fill_qty=0.5,
                    fill_price=65100.0,
                    cumulative_qty=0.5,
                    cumulative_quote_qty=32550.0,
                    fee_cost=0.05,
                    fee_currency="USDT",
                    is_maker=True,
                    reduce_only=True,
                ),
            ),
        )
        seen_event_types.extend(
            await _drain_runtime_events(event_bus, position_service)
        )
        await execution_service.stop()
        await position_service.stop()
        return seen_event_types

    seen_event_types = asyncio.run(run())

    state = execution_service.get_order_state("intent-1")
    assert state is not None
    assert state.status.value == "filled"
    assert execution_service.get_protection_state("intent-1") is not None
    protection_state = execution_service.get_protection_state("intent-1")
    assert protection_state is not None
    assert protection_state.status.value == "native_submitted"
    position_state = position_service.get_position_state("intent-1")
    assert position_state is not None
    assert position_state.status.value == "closed"
    assert position_state.protective_order_ids == (
        "protect-order-sl",
        "protect-order-tp",
    )
    assert "position.opened" in seen_event_types
    assert "position.closed" in seen_event_types
    assert execution_gateway.protection_calls


def test_phase3_restart_recovery_rehydrates_services() -> None:
    open_trades = {
        "BTC/USDT": {
            "db_id": 1,
            "symbol": "BTC/USDT",
            "side": "buy",
            "amount": 0.5,
            "entry_price": 65000.0,
            "stop_loss": 63000.0,
            "take_profit": 66000.0,
            "timestamp": datetime.now(timezone.utc),
            "order_id": "order-1",
            "status": "open",
            "fees": 0.1,
            "trailing_stop_price": None,
        }
    }
    snapshots = {
        "order-1": OrderUpdatePayload(
            client_order_id="order-1",
            exchange_order_id="order-1",
            symbol="BTC/USDT",
            status="filled",
            filled_qty=0.5,
            remaining_qty=0.0,
            avg_fill_price=65000.0,
            reduce_only=False,
        )
    }
    trade_store = FakeTradeStore(open_trades)
    execution_gateway = FakeExecutionGateway()
    execution_gateway.snapshots = snapshots
    event_bus: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    execution_service = ExecutionService(
        name="execution",
        execution_gateway=execution_gateway,
        event_bus=event_bus,
    )
    position_service = PositionService(name="position", event_bus=event_bus)
    recovery_service = RestartRecoveryService(
        name="recovery",
        trade_store=trade_store,
        execution_gateway=execution_gateway,
        execution_service=execution_service,
        position_service=position_service,
        max_recovery_items=10,
        fetch_timeout_seconds=1.0,
        reapply_native_protection=True,
    )

    async def run() -> None:
        await recovery_service.start()
        report = await recovery_service.recover()
        await recovery_service.stop()
        assert report.restored_symbols == ("BTC/USDT",)
        assert report.protected_symbols == ("BTC/USDT",)
        assert report.orphaned_symbols == ()
        assert report.unprotected_symbols == ()

    asyncio.run(run())

    restored_order = execution_service.get_order_state("order-1")
    assert restored_order is not None
    assert restored_order.status.value == "filled"
    restored_position = position_service.get_position_for_symbol("BTC/USDT")
    assert restored_position is not None
    assert restored_position.status.value == "protected"
    assert restored_position.protective_order_ids == (
        "protect-order-sl",
        "protect-order-tp",
    )
    assert trade_store.calls == 1
