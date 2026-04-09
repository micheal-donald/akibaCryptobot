from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from domain.events import (
    EventMeta,
    KlinePayload,
    OrderIntentPayload,
    OrderSubmittedPayload,
    OrderUpdatePayload,
    RuntimeEvent,
)
from runtime.app import RuntimeApp
from runtime.config import RuntimeConfig
from runtime.projection import build_runtime_projection
from strategies.base_strategy import BaseStrategy, SignalType, TradingSignal
from strategies.runtime_adapter import LegacyStrategyRuntimeAdapter


@dataclass
class MemoryRuntimeStore:
    events: list[object]
    reports: list[object]
    initialize_calls: int = 0
    close_calls: int = 0

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def record_event(self, event: object) -> None:
        self.events.append(event)

    async def record_recovery_report(self, report: object) -> None:
        self.reports.append(report)


class FakeMarketDataGateway:
    def __init__(self) -> None:
        self.callbacks: dict[str, list[object]] = {}
        self.calls: list[object] = []

    async def connect(self) -> None:
        self.calls.append("connect")

    async def disconnect(self) -> None:
        self.calls.append("disconnect")

    def register_callback(self, event_type: str, callback: object) -> None:
        self.callbacks.setdefault(event_type, []).append(callback)

    async def subscribe_book_ticker(self, symbol: str) -> None:
        self.calls.append(("book_ticker", symbol))

    async def subscribe_kline(self, symbol: str, interval: str = "1m") -> None:
        self.calls.append(("kline", symbol, interval))

    async def emit(self, event_type: str, event: object) -> None:
        for callback in self.callbacks[event_type]:
            result = callback(event)
            if inspect.isawaitable(result):
                await result


class FakeUserStreamGateway:
    def __init__(self) -> None:
        self.connected = 0
        self.disconnected = 0
        self.subscribed = 0

    async def connect(self) -> None:
        self.connected += 1

    async def disconnect(self) -> None:
        self.disconnected += 1

    async def subscribe_user_stream(self) -> None:
        self.subscribed += 1

    def register_order_update_handler(self, handler: object) -> None:
        del handler

    def register_fill_handler(self, handler: object) -> None:
        del handler

    def register_balance_update_handler(self, handler: object) -> None:
        del handler


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
        self.connected = 0
        self.disconnected = 0
        self.fetch_calls: list[tuple[str, str]] = []
        self.submit_protection_calls: list[tuple[str, str, float]] = []
        self.submissions: list[OrderIntentPayload] = []

    async def connect(self) -> None:
        self.connected += 1

    async def disconnect(self) -> None:
        self.disconnected += 1

    async def submit_order(self, intent: OrderIntentPayload) -> OrderSubmittedPayload:
        self.submissions.append(intent)
        return OrderSubmittedPayload(
            client_order_id=intent.intent_id,
            exchange_order_id=f"submitted-{len(self.submissions)}",
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            requested_qty=intent.quantity,
            requested_price=intent.limit_price,
            reduce_only=intent.reduce_only,
        )

    async def cancel_order(self, symbol: str, exchange_order_id: str):
        del symbol, exchange_order_id

    async def fetch_order(
        self, symbol: str, exchange_order_id: str
    ) -> OrderUpdatePayload:
        self.fetch_calls.append((symbol, exchange_order_id))
        return self.snapshots[exchange_order_id]

    async def submit_protection(
        self,
        symbol: str,
        side: str,
        quantity: float,
        protection: object,
    ) -> tuple[OrderSubmittedPayload, ...]:
        del protection
        self.submit_protection_calls.append((symbol, side, quantity))
        return (
            OrderSubmittedPayload(
                client_order_id="protect-BTC/USDT-stop_loss_limit",
                exchange_order_id="protect-order-sl",
                symbol=symbol,
                side=side,
                order_type="stop_loss_limit",
                requested_qty=quantity,
                requested_price=63000.0,
                reduce_only=True,
            ),
            OrderSubmittedPayload(
                client_order_id="protect-BTC/USDT-take_profit_limit",
                exchange_order_id="protect-order-tp",
                symbol=symbol,
                side=side,
                order_type="take_profit_limit",
                requested_qty=quantity,
                requested_price=66000.0,
                reduce_only=True,
            ),
        )


class NullRuntimeStrategy:
    async def generate_intent(self, snapshot: object):
        del snapshot
        return None


class ExitOnlyLegacyStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__({})
        self.name = "phase18_exit_only"

    async def generate_signal(
        self, symbol: str, ohlcv_data: pd.DataFrame, current_price: float
    ) -> TradingSignal | None:
        del symbol, ohlcv_data, current_price
        return None

    def calculate_indicators(self, ohlcv_data: pd.DataFrame) -> pd.DataFrame:
        return ohlcv_data

    async def should_exit_position(
        self,
        symbol: str,
        current_position: dict,
        current_price: float,
        ohlcv_data: pd.DataFrame,
    ) -> TradingSignal | None:
        assert symbol == "BTC/USDT"
        assert current_position["amount"] == 0.5
        assert current_price == 65050.0
        assert not ohlcv_data.empty
        return TradingSignal(
            signal_type=SignalType.CLOSE_LONG,
            strength=1.0,
            price=current_price,
            timestamp=datetime(2026, 4, 9, 8, 20, tzinfo=timezone.utc),
            metadata={"exit_reason": "phase18_restart_exit"},
        )


def make_config(*, runtime_mode: str = "runtime_primary") -> RuntimeConfig:
    return RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        queue_maxsize=4,
        runtime_mode=runtime_mode,
        shadow_mode=False,
    )


def make_kline_event() -> RuntimeEvent:
    close_time = datetime.now(timezone.utc).replace(microsecond=0)
    open_time = close_time.replace(second=0, microsecond=0)
    return RuntimeEvent(
        meta=EventMeta(
            event_id="evt-phase18-kline-1",
            event_type="market.kline_closed",
            source="binance.ws.kline.1m",
            symbol="BTCUSDT",
            ts_exchange=close_time,
            ts_received=close_time,
        ),
        payload=KlinePayload(
            interval="1m",
            open_time=open_time,
            close_time=close_time,
            open=65000.0,
            high=65100.0,
            low=64950.0,
            close=65050.0,
            volume=12.5,
            is_closed=True,
        ),
    )


async def wait_for(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        if predicate():
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("condition not met before timeout")
        await asyncio.sleep(0.01)


def test_phase18_startup_recovers_carried_runtime_positions_and_persists_report() -> None:
    store = MemoryRuntimeStore(events=[], reports=[])
    trade_store = FakeTradeStore(
        {
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
    )
    execution_gateway = FakeRecoveryExecutionGateway(
        {
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
    )
    app = RuntimeApp.build_phase5(
        make_config(),
        market_data_gateway=FakeMarketDataGateway(),
        execution_gateway=execution_gateway,
        runtime_store=store,
        strategy=NullRuntimeStrategy(),
        trade_store=trade_store,
        user_stream_gateway=FakeUserStreamGateway(),
    )

    async def run() -> None:
        await app.start()
        projection = build_runtime_projection(
            runtime_mode=app.config.runtime_mode,
            runtime_is_running=app.is_running,
            runtime_start_status="running",
            position_source=app.position_service,
            execution_source=app.execution_service,
        )
        assert app.risk_service is not None
        assert app.risk_service.entries_paused is False
        assert projection.active_position_count == 1
        assert projection.protected_position_count == 1
        assert projection.positions[0].symbol == "BTC/USDT"
        assert projection.positions[0].status == "protected"
        assert projection.positions[0].protective_order_ids == (
            "protect-order-sl",
            "protect-order-tp",
        )
        assert app.recovery_report is not None
        assert app.recovery_report.restored_symbols == ("BTC/USDT",)
        assert app.recovery_report.protected_symbols == ("BTC/USDT",)
        assert len(store.reports) == 1
        await app.stop()

    asyncio.run(run())

    assert trade_store.calls == 1
    assert store.initialize_calls == 1
    assert store.close_calls == 1
    assert execution_gateway.connected == 1
    assert execution_gateway.disconnected == 1
    assert execution_gateway.fetch_calls == [("BTC/USDT", "order-1")]
    assert execution_gateway.submit_protection_calls == [("BTC/USDT", "buy", 0.5)]


def test_phase18_startup_without_trade_store_skips_recovery_cleanly() -> None:
    store = MemoryRuntimeStore(events=[], reports=[])
    app = RuntimeApp.build_phase5(
        make_config(runtime_mode="runtime_entries"),
        market_data_gateway=FakeMarketDataGateway(),
        execution_gateway=FakeRecoveryExecutionGateway({}),
        runtime_store=store,
        strategy=NullRuntimeStrategy(),
        user_stream_gateway=FakeUserStreamGateway(),
    )

    async def run() -> None:
        await app.start()
        assert app.recovery_service is None
        assert app.recovery_report is None
        assert store.reports == []
        await app.stop()

    asyncio.run(run())


def test_phase18_runtime_primary_can_exit_recovered_position_after_restart() -> None:
    store = MemoryRuntimeStore(events=[], reports=[])
    trade_store = FakeTradeStore(
        {
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
            }
        }
    )
    execution_gateway = FakeRecoveryExecutionGateway(
        {
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
    )
    strategy = LegacyStrategyRuntimeAdapter(ExitOnlyLegacyStrategy())
    market_data_gateway = FakeMarketDataGateway()
    app = RuntimeApp.build_phase5(
        make_config(),
        market_data_gateway=market_data_gateway,
        execution_gateway=execution_gateway,
        runtime_store=store,
        strategy=strategy,
        trade_store=trade_store,
        user_stream_gateway=FakeUserStreamGateway(),
    )

    async def run() -> None:
        await app.start()
        await market_data_gateway.emit("market.kline_closed", make_kline_event())
        await wait_for(lambda: len(execution_gateway.submissions) == 1)
        assert await app.persistence_service.wait_until_drained(timeout_seconds=1.0)
        await app.stop()

    asyncio.run(run())

    submitted = execution_gateway.submissions[0]
    assert submitted.symbol == "BTC/USDT"
    assert submitted.side == "sell"
    assert submitted.quantity == 0.5
    assert submitted.reduce_only is True
    assert submitted.reason == "phase18_restart_exit"
