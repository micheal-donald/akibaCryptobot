from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from domain.events import (
    EventMeta,
    KlinePayload,
    OrderIntentPayload,
    OrderSubmittedPayload,
    RuntimeEvent,
)
from runtime.app import RuntimeApp
from runtime.config import RuntimeConfig


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

    async def emit(self, event_type: str, event: RuntimeEvent) -> None:
        for callback in self.callbacks[event_type]:
            result = callback(event)
            if inspect.isawaitable(result):
                await result


class FakeExecutionGateway:
    def __init__(self) -> None:
        self.connected = 0
        self.disconnected = 0
        self.submissions: list[OrderIntentPayload] = []

    async def connect(self) -> None:
        self.connected += 1

    async def disconnect(self) -> None:
        self.disconnected += 1

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


class FakeRuntimeStrategy:
    def __init__(self, *, blocked: bool = False) -> None:
        self.blocked = blocked
        self.snapshots: list[object] = []

    async def generate_intent(self, snapshot: object) -> OrderIntentPayload | None:
        self.snapshots.append(snapshot)
        return OrderIntentPayload(
            intent_id="signal-intent-1",
            symbol=snapshot.symbol,  # type: ignore[attr-defined]
            side="buy",
            order_type="market",
            quantity=0.0 if self.blocked else 0.25,
            strategy="phase5-test",
        )


class FakeDepthMarketDataGateway(FakeMarketDataGateway):
    async def subscribe_depth(self, symbol: str, depth: int = 10) -> None:
        self.calls.append(("depth", symbol, depth))

    async def subscribe_trade(self, symbol: str) -> None:
        self.calls.append(("trade", symbol))


def make_config() -> RuntimeConfig:
    return RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        queue_maxsize=4,
        shadow_mode=False,
    )


def make_kline_event() -> RuntimeEvent:
    close_time = datetime.now(timezone.utc).replace(microsecond=0)
    open_time = close_time - timedelta(minutes=1)
    return RuntimeEvent(
        meta=EventMeta(
            event_id="evt-kline-1",
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


def test_phase5_runtime_shell_routes_signal_to_execution() -> None:
    market_data_gateway = FakeMarketDataGateway()
    execution_gateway = FakeExecutionGateway()
    user_stream_gateway = FakeUserStreamGateway()
    store = MemoryRuntimeStore(events=[], reports=[])
    strategy = FakeRuntimeStrategy()
    app = RuntimeApp.build_phase5(
        make_config(),
        market_data_gateway=market_data_gateway,
        execution_gateway=execution_gateway,
        runtime_store=store,
        strategy=strategy,
        user_stream_gateway=user_stream_gateway,
    )

    async def run() -> None:
        await app.start()
        await market_data_gateway.emit("market.kline_closed", make_kline_event())
        await wait_for(lambda: len(execution_gateway.submissions) == 1)
        assert await app.persistence_service.wait_until_drained(timeout_seconds=1.0)
        await app.stop()

    asyncio.run(run())

    assert len(strategy.snapshots) == 1
    assert execution_gateway.submissions[0].quantity == 0.25
    assert [event.meta.event_type for event in store.events] == [
        "market.kline_closed",
        "execution.order_submitted",
    ]


def test_phase5_runtime_shell_routes_blocked_signal_to_risk_without_execution() -> None:
    market_data_gateway = FakeMarketDataGateway()
    execution_gateway = FakeExecutionGateway()
    user_stream_gateway = FakeUserStreamGateway()
    store = MemoryRuntimeStore(events=[], reports=[])
    strategy = FakeRuntimeStrategy(blocked=True)
    app = RuntimeApp.build_phase5(
        make_config(),
        market_data_gateway=market_data_gateway,
        execution_gateway=execution_gateway,
        runtime_store=store,
        strategy=strategy,
        user_stream_gateway=user_stream_gateway,
    )

    async def run() -> None:
        await app.start()
        await market_data_gateway.emit("market.kline_closed", make_kline_event())
        assert await app.persistence_service.wait_until_drained(timeout_seconds=1.0)
        await app.stop()

    asyncio.run(run())

    assert execution_gateway.submissions == []
    assert [event.meta.event_type for event in store.events] == [
        "market.kline_closed",
        "risk.blocked",
    ]


def test_phase5_skips_microstructure_when_gateway_lacks_depth_streams() -> None:
    app = RuntimeApp.build_phase5(
        RuntimeConfig(
            symbols=("BTC/USDT",),
            kline_intervals=("1m",),
            shadow_mode=False,
            microstructure_enabled=True,
        ),
        market_data_gateway=FakeMarketDataGateway(),
        execution_gateway=FakeExecutionGateway(),
        runtime_store=MemoryRuntimeStore(events=[], reports=[]),
        strategy=FakeRuntimeStrategy(),
        user_stream_gateway=FakeUserStreamGateway(),
    )

    assert app.microstructure_service is None
    assert "microstructure-shadow" not in app.services.names()


def test_phase5_wires_microstructure_shadow_and_persists_decisions() -> None:
    from domain.events import DepthSnapshotPayload, TradePayload

    market_data_gateway = FakeDepthMarketDataGateway()
    store = MemoryRuntimeStore(events=[], reports=[])
    app = RuntimeApp.build_phase5(
        RuntimeConfig(
            symbols=("BTC/USDT",),
            kline_intervals=("1m",),
            shadow_mode=False,
            microstructure_enabled=True,
            microstructure_strategy_params={"min_confidence": 0.1},
        ),
        market_data_gateway=market_data_gateway,
        execution_gateway=FakeExecutionGateway(),
        runtime_store=store,
        strategy=FakeRuntimeStrategy(),
        user_stream_gateway=FakeUserStreamGateway(),
    )

    assert app.microstructure_service is not None
    assert "microstructure-shadow" in app.services.names()

    def depth_event(sequence: int) -> RuntimeEvent:
        return RuntimeEvent(
            meta=EventMeta(
                event_id=f"evt-depth-{sequence}",
                event_type="market.depth",
                source="binance.ws.depth",
                symbol="BTCUSDT",
                ts_exchange=None,
                ts_received=datetime.now(timezone.utc),
            ),
            payload=DepthSnapshotPayload(
                bids=((100.0, 50.0), (99.9, 40.0)),
                asks=((100.01, 1.0), (100.05, 1.0)),
                sequence=sequence,
            ),
        )

    def trade_event(index: int) -> RuntimeEvent:
        return RuntimeEvent(
            meta=EventMeta(
                event_id=f"evt-trade-{index}",
                event_type="market.trade",
                source="binance.ws.trade",
                symbol="BTCUSDT",
                ts_exchange=None,
                ts_received=datetime.now(timezone.utc),
            ),
            payload=TradePayload(price=100.0, quantity=1.0, side="buy"),
        )

    async def run() -> None:
        await app.start()
        assert ("depth", "BTCUSDT", 10) in market_data_gateway.calls
        assert ("trade", "BTCUSDT") in market_data_gateway.calls
        for index in range(3):
            await market_data_gateway.emit("market.trade", trade_event(index))
        await market_data_gateway.emit("market.depth", depth_event(7))
        await wait_for(
            lambda: any(
                event.meta.event_type == "microstructure.decision"
                for event in store.events
            )
        )
        await app.stop()

    asyncio.run(run())

    status = app.microstructure_service.get_status()
    assert status["decision_counts"].get("buy", 0) >= 1
    decision_events = [
        event
        for event in store.events
        if event.meta.event_type == "microstructure.decision"
    ]
    assert decision_events
    assert decision_events[0].meta.symbol == "BTC/USDT"
