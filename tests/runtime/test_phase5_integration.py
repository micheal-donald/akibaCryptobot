from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from domain.events import (
    EventMeta,
    KlinePayload,
    OrderIntentPayload,
    OrderSubmittedPayload,
    RuntimeEvent,
)
from runtime.app import RuntimeApp
from runtime.config import RuntimeConfig
from strategies.base_strategy import BaseStrategy, SignalType, TradingSignal
from strategies.runtime_adapter import LegacyStrategyRuntimeAdapter


@dataclass
class MemoryRuntimeStore:
    events: list[RuntimeEvent]
    reports: list[object]

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def record_event(self, event: RuntimeEvent) -> None:
        self.events.append(event)

    async def record_recovery_report(self, report: object) -> None:
        self.reports.append(report)


class FakeMarketDataGateway:
    def __init__(self) -> None:
        self.callbacks: dict[str, list[object]] = {}

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    def register_callback(self, event_type: str, callback: object) -> None:
        self.callbacks.setdefault(event_type, []).append(callback)

    async def subscribe_book_ticker(self, symbol: str) -> None:
        del symbol

    async def subscribe_kline(self, symbol: str, interval: str = "1m") -> None:
        del symbol, interval

    async def emit(self, event_type: str, event: RuntimeEvent) -> None:
        for callback in self.callbacks[event_type]:
            result = callback(event)
            if inspect.isawaitable(result):
                await result


class FakeExecutionGateway:
    def __init__(self) -> None:
        self.submissions: list[OrderIntentPayload] = []

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def submit_order(self, intent: OrderIntentPayload) -> OrderSubmittedPayload:
        self.submissions.append(intent)
        return OrderSubmittedPayload(
            client_order_id=intent.intent_id,
            exchange_order_id="order-1",
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            requested_qty=intent.quantity,
            requested_price=intent.limit_price,
            reduce_only=intent.reduce_only,
        )

    async def submit_protection(
        self,
        symbol: str,
        side: str,
        quantity: float,
        protection: object,
    ) -> tuple[OrderSubmittedPayload, ...]:
        del symbol, side, quantity, protection
        return ()

    async def cancel_order(self, symbol: str, exchange_order_id: str):
        del symbol, exchange_order_id

    async def fetch_order(self, symbol: str, exchange_order_id: str):
        del symbol, exchange_order_id


class FakeUserStreamGateway:
    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def subscribe_user_stream(self) -> None:
        return None

    def register_order_update_handler(self, handler: object) -> None:
        del handler

    def register_fill_handler(self, handler: object) -> None:
        del handler

    def register_balance_update_handler(self, handler: object) -> None:
        del handler


class FakeLegacyStrategy(BaseStrategy):
    def __init__(self, *, signal: TradingSignal | None = None) -> None:
        super().__init__({})
        self._signal = signal
        self.name = "legacy_test"

    async def generate_signal(
        self, symbol: str, ohlcv_data: pd.DataFrame, current_price: float
    ) -> TradingSignal | None:
        assert symbol == "BTC/USDT"
        assert not ohlcv_data.empty
        assert current_price == 65050.0
        return self._signal

    def calculate_indicators(self, ohlcv_data: pd.DataFrame) -> pd.DataFrame:
        return ohlcv_data


class RaisingRuntimeStrategy:
    async def generate_intent(self, snapshot: object) -> OrderIntentPayload | None:
        del snapshot
        raise RuntimeError("boom")


def make_config() -> RuntimeConfig:
    return RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        queue_maxsize=4,
        shadow_mode=False,
    )


def make_kline_event(*, event_id: str = "evt-kline-1") -> RuntimeEvent:
    close_time = datetime.now(timezone.utc).replace(microsecond=0)
    open_time = close_time - timedelta(minutes=1)
    return RuntimeEvent(
        meta=EventMeta(
            event_id=event_id,
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


def test_phase5_integration_runtime_entry_flow_uses_legacy_adapter() -> None:
    market_data_gateway = FakeMarketDataGateway()
    execution_gateway = FakeExecutionGateway()
    store = MemoryRuntimeStore(events=[], reports=[])
    legacy_strategy = FakeLegacyStrategy(
        signal=TradingSignal(
            signal_type=SignalType.BUY,
            strength=0.9,
            price=65050.0,
            timestamp=datetime(2026, 3, 24, 9, 1, tzinfo=timezone.utc),
            stop_loss=64000.0,
            take_profit=67000.0,
        )
    )
    strategy = LegacyStrategyRuntimeAdapter(legacy_strategy, entry_quantity=0.25)
    app = RuntimeApp.build_phase5(
        make_config(),
        market_data_gateway=market_data_gateway,
        execution_gateway=execution_gateway,
        runtime_store=store,
        strategy=strategy,
        user_stream_gateway=FakeUserStreamGateway(),
    )

    async def run() -> None:
        await app.start()
        await market_data_gateway.emit("market.kline_closed", make_kline_event())
        await wait_for(lambda: len(execution_gateway.submissions) == 1)
        assert await app.persistence_service.wait_until_drained(timeout_seconds=1.0)
        await app.stop()

    asyncio.run(run())

    assert execution_gateway.submissions[0].quantity == 0.25
    assert execution_gateway.submissions[0].stop_loss == 64000.0
    assert execution_gateway.submissions[0].take_profit == 67000.0
    assert execution_gateway.submissions[0].strategy == "legacy_test"
    assert [event.meta.event_type for event in store.events] == [
        "signal.order_intent",
        "execution.order_submitted",
    ]


def test_phase5_integration_duplicate_event_is_suppressed_once() -> None:
    market_data_gateway = FakeMarketDataGateway()
    execution_gateway = FakeExecutionGateway()
    store = MemoryRuntimeStore(events=[], reports=[])
    legacy_strategy = FakeLegacyStrategy(
        signal=TradingSignal(
            signal_type=SignalType.BUY,
            strength=0.9,
            price=65050.0,
            timestamp=datetime(2026, 3, 24, 9, 1, tzinfo=timezone.utc),
        )
    )
    strategy = LegacyStrategyRuntimeAdapter(legacy_strategy, entry_quantity=0.25)
    app = RuntimeApp.build_phase5(
        make_config(),
        market_data_gateway=market_data_gateway,
        execution_gateway=execution_gateway,
        runtime_store=store,
        strategy=strategy,
        user_stream_gateway=FakeUserStreamGateway(),
    )

    async def run() -> None:
        await app.start()
        event = make_kline_event()
        await market_data_gateway.emit("market.kline_closed", event)
        await market_data_gateway.emit("market.kline_closed", event)
        await wait_for(lambda: len(execution_gateway.submissions) == 1)
        assert await app.persistence_service.wait_until_drained(timeout_seconds=1.0)
        await app.stop()

    asyncio.run(run())

    assert len(execution_gateway.submissions) == 1
    event_types = [event.meta.event_type for event in store.events]
    assert event_types.count("execution.order_submitted") == 1
    assert "signal.suppressed" not in event_types
    assert (
        app.signal_service.get_suppression_counts().get("duplicate_candle_close") == 1
    )


def test_phase5_integration_strategy_exception_is_isolated() -> None:
    market_data_gateway = FakeMarketDataGateway()
    execution_gateway = FakeExecutionGateway()
    store = MemoryRuntimeStore(events=[], reports=[])
    app = RuntimeApp.build_phase5(
        make_config(),
        market_data_gateway=market_data_gateway,
        execution_gateway=execution_gateway,
        runtime_store=store,
        strategy=RaisingRuntimeStrategy(),
        user_stream_gateway=FakeUserStreamGateway(),
    )

    async def run() -> None:
        await app.start()
        await market_data_gateway.emit("market.kline_closed", make_kline_event())
        assert await app.persistence_service.wait_until_drained(timeout_seconds=1.0)
        await app.stop()

    asyncio.run(run())

    assert execution_gateway.submissions == []
    assert [event.meta.event_type for event in store.events] == []
