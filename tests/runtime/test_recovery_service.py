from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from domain.events import OrderUpdatePayload
from domain.events import OrderSubmittedPayload
from services.execution_service import ExecutionService
from services.position_service import PositionService
from services.recovery_service import RestartRecoveryService


class FakeTradeStore:
    def __init__(self, open_trades: dict[str, dict]) -> None:
        self.open_trades = open_trades
        self.calls = 0

    async def load_open_trades(self) -> dict[str, dict]:
        self.calls += 1
        return self.open_trades


class FakeRecoveryExecutionGateway:
    def __init__(self, snapshots: dict[str, OrderUpdatePayload]) -> None:
        self.snapshots = snapshots
        self.fetch_calls: list[tuple[str, str]] = []
        self.protection_response: tuple[OrderSubmittedPayload, ...] = (
            OrderSubmittedPayload(
                client_order_id="protect-BTC/USDT-stop_loss_limit",
                exchange_order_id="protect-order-sl",
                symbol="BTC/USDT",
                side="sell",
                order_type="stop_loss_limit",
                requested_qty=0.5,
                requested_price=63000.0,
                reduce_only=True,
            ),
            OrderSubmittedPayload(
                client_order_id="protect-BTC/USDT-take_profit_limit",
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
        return None

    async def disconnect(self) -> None:
        return None

    async def submit_order(self, intent):  # pragma: no cover - unused in recovery
        del intent
        raise AssertionError("submit_order should not be called in recovery")

    async def cancel_order(
        self, symbol: str, exchange_order_id: str
    ):  # pragma: no cover - unused
        del symbol, exchange_order_id
        raise AssertionError("cancel_order should not be called in recovery")

    async def fetch_order(
        self, symbol: str, exchange_order_id: str
    ) -> OrderUpdatePayload:
        self.fetch_calls.append((symbol, exchange_order_id))
        return self.snapshots[exchange_order_id]

    async def submit_protection(self, symbol, side, quantity, protection):
        del symbol, side, quantity, protection
        return self.protection_response


def build_services(
    open_trades: dict[str, dict],
    snapshots: dict[str, OrderUpdatePayload],
    *,
    reapply_native_protection: bool = False,
) -> tuple[RestartRecoveryService, ExecutionService, PositionService, FakeTradeStore]:
    trade_store = FakeTradeStore(open_trades)
    execution_gateway = FakeRecoveryExecutionGateway(snapshots)
    event_bus: asyncio.Queue = asyncio.Queue()
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
        reapply_native_protection=reapply_native_protection,
    )
    return recovery_service, execution_service, position_service, trade_store


def test_recovery_restores_open_trades_without_reapplying_native_protection() -> None:
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
    recovery_service, execution_service, position_service, trade_store = build_services(
        open_trades,
        snapshots,
    )

    async def run() -> None:
        await recovery_service.start()
        report = await recovery_service.recover()
        await recovery_service.stop()
        assert report.restored_symbols == ("BTC/USDT",)
        assert report.protected_symbols == ()
        assert report.orphaned_symbols == ()
        assert report.unprotected_symbols == ("BTC/USDT",)
        assert report.reconcile_error_symbols == ()

    asyncio.run(run())

    assert trade_store.calls == 1
    order_state = execution_service.get_order_state("order-1")
    assert order_state is not None
    assert order_state.status.value == "filled"
    position_state = position_service.get_position_for_symbol("BTC/USDT")
    assert position_state is not None
    assert position_state.status.value == "protective_pending"
    assert position_state.protective_order_ids == ()


def test_recovery_can_reapply_native_protection_when_explicitly_enabled() -> None:
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
    recovery_service, execution_service, position_service, _ = build_services(
        open_trades,
        snapshots,
        reapply_native_protection=True,
    )

    async def run() -> None:
        await recovery_service.start()
        report = await recovery_service.recover()
        await recovery_service.stop()
        assert report.restored_symbols == ("BTC/USDT",)
        assert report.protected_symbols == ("BTC/USDT",)
        assert report.unprotected_symbols == ()

    asyncio.run(run())

    protection_state = execution_service.get_protection_state("order-1")
    assert protection_state is not None
    assert protection_state.status.value == "native_submitted"
    position_state = position_service.get_position_for_symbol("BTC/USDT")
    assert position_state is not None
    assert position_state.status.value == "protected"
    assert position_state.protective_order_ids == (
        "protect-order-sl",
        "protect-order-tp",
    )


def test_recovery_marks_orphaned_and_unprotected_positions() -> None:
    open_trades = {
        "ETH/USDT": {
            "db_id": 2,
            "symbol": "ETH/USDT",
            "side": "sell",
            "amount": 1.0,
            "entry_price": 3000.0,
            "stop_loss": 3200.0,
            "take_profit": 2800.0,
            "timestamp": datetime.now(timezone.utc),
            "order_id": "order-2",
            "status": "open",
            "fees": 0.1,
            "trailing_stop_price": None,
        }
    }
    snapshots = {
        "order-2": OrderUpdatePayload(
            client_order_id="order-2",
            exchange_order_id="order-2",
            symbol="ETH/USDT",
            status="cancelled",
            filled_qty=0.0,
            remaining_qty=1.0,
            avg_fill_price=None,
            reduce_only=False,
            raw_status="CANCELED",
        )
    }
    recovery_service, execution_service, position_service, _ = build_services(
        open_trades,
        snapshots,
    )

    async def run() -> None:
        await recovery_service.start()
        report = await recovery_service.recover()
        await recovery_service.stop()
        assert report.orphaned_symbols == ("ETH/USDT",)
        assert report.reconcile_error_symbols == ("ETH/USDT",)
        assert report.unprotected_symbols == ()

    asyncio.run(run())

    order_state = execution_service.get_order_state("order-2")
    assert order_state is not None
    assert order_state.status.value == "orphaned"
    position_state = position_service.get_position_state("order-2")
    assert position_state is not None
    assert position_state.status.value == "reconcile_error"


def test_recovery_skips_extra_trades_when_bounded() -> None:
    open_trades = {
        "BTC/USDT": {
            "db_id": 1,
            "symbol": "BTC/USDT",
            "side": "buy",
            "amount": 0.5,
            "entry_price": 65000.0,
            "stop_loss": None,
            "take_profit": None,
            "timestamp": datetime.now(timezone.utc),
            "order_id": "order-1",
            "status": "open",
            "fees": 0.1,
            "trailing_stop_price": None,
        },
        "ETH/USDT": {
            "db_id": 2,
            "symbol": "ETH/USDT",
            "side": "sell",
            "amount": 1.0,
            "entry_price": 3000.0,
            "stop_loss": None,
            "take_profit": None,
            "timestamp": datetime.now(timezone.utc),
            "order_id": "order-2",
            "status": "open",
            "fees": 0.1,
            "trailing_stop_price": None,
        },
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
        ),
        "order-2": OrderUpdatePayload(
            client_order_id="order-2",
            exchange_order_id="order-2",
            symbol="ETH/USDT",
            status="filled",
            filled_qty=1.0,
            remaining_qty=0.0,
            avg_fill_price=3000.0,
            reduce_only=False,
        ),
    }
    recovery_service, _, _, trade_store = build_services(open_trades, snapshots)
    recovery_service.max_recovery_items = 1

    async def run() -> None:
        await recovery_service.start()
        report = await recovery_service.recover()
        await recovery_service.stop()
        assert report.restored_symbols == ("BTC/USDT",)
        assert report.skipped_symbols == ("ETH/USDT",)

    asyncio.run(run())

    assert trade_store.calls == 1
