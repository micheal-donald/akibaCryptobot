from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from domain.events import EventMeta, KlinePayload, OrderIntentPayload, RuntimeEvent
from domain.state import PositionState, PositionStatus
from domain.models import SymbolState
from services.signal_service import SignalService, SignalSuppressedPayload
from services.telemetry_service import TelemetryService


def make_kline_event(
    *,
    event_id: str = "evt-1",
    symbol: str = "BTC/USDT",
    interval: str = "1m",
    open_time: datetime | None = None,
    close_time: datetime | None = None,
    is_closed: bool = True,
) -> RuntimeEvent:
    open_time = open_time or datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc)
    close_time = close_time or datetime(2026, 3, 22, 9, 1, tzinfo=timezone.utc)
    return RuntimeEvent(
        meta=EventMeta(
            event_id=event_id,
            event_type="market.kline_closed",
            source="binance.ws.kline.1m",
            symbol=symbol,
            ts_exchange=close_time,
            ts_received=datetime(2026, 3, 22, 9, 2, tzinfo=timezone.utc),
        ),
        payload=KlinePayload(
            interval=interval,
            open_time=open_time,
            close_time=close_time,
            open=65000.0,
            high=65100.0,
            low=64950.0,
            close=65050.0,
            volume=12.5,
            is_closed=is_closed,
        ),
    )


class FakeMarketStateSource:
    def __init__(self, state: SymbolState | None = None) -> None:
        self.state = state

    def get_symbol_state(self, symbol: str) -> SymbolState | None:
        if self.state is None or self.state.symbol != symbol:
            return None
        return self.state


class FakePositionSource:
    def __init__(self, position: PositionState | None = None) -> None:
        self.position = position

    def get_position_for_symbol(self, symbol: str) -> PositionState | None:
        if self.position is None or self.position.symbol != symbol:
            return None
        return self.position


class FakeRuntimeStrategy:
    def __init__(self, *, mode: str = "intent") -> None:
        self.mode = mode
        self.snapshots: list[object] = []

    async def generate_intent(self, snapshot: object) -> OrderIntentPayload | None:
        self.snapshots.append(snapshot)
        if self.mode == "raise":
            raise RuntimeError("strategy failed")
        if self.mode == "none":
            return None
        return OrderIntentPayload(
            intent_id="intent-1",
            symbol=snapshot.symbol,  # type: ignore[attr-defined]
            side="buy",
            order_type="market",
            quantity=0.01,
        )


async def _run_service_once(
    service: SignalService,
    source: asyncio.Queue[RuntimeEvent],
    event: RuntimeEvent,
) -> None:
    await service.start()
    await source.put(event)
    await asyncio.wait_for(source.join(), timeout=1.0)
    await service.stop()


def test_signal_service_emits_order_intent_and_builds_snapshot() -> None:
    source: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    output: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    market_state = FakeMarketStateSource(
        state=SymbolState(
            symbol="BTC/USDT",
            best_bid=65040.0,
            best_ask=65060.0,
            last_price=65050.0,
        )
    )
    position = PositionState(
        position_id="pos-1",
        symbol="BTC/USDT",
        side="buy",
        requested_qty=0.01,
        status=PositionStatus.OPEN,
        open_qty=0.01,
    )
    service = SignalService(
        name="signal",
        strategy=FakeRuntimeStrategy(),
        event_bus=output,
        event_source=source,
        market_state_source=market_state,
        position_source=FakePositionSource(position),
        supported_timeframes=("1m",),
    )

    async def run() -> None:
        await _run_service_once(service, source, make_kline_event())

    asyncio.run(run())

    event = output.get_nowait()
    assert event.meta.event_type == "signal.order_intent"
    assert isinstance(event.payload, OrderIntentPayload)
    assert event.payload.strategy == "FakeRuntimeStrategy"
    assert service.get_evaluation_counts() == {"evaluated": 1, "generated": 1}
    assert service.get_suppression_counts() == {}
    snapshot = service.strategy.snapshots[0]
    assert snapshot.symbol == "BTC/USDT"
    assert snapshot.timeframe == "1m"
    assert snapshot.best_bid == 65040.0
    assert snapshot.best_ask == 65060.0
    assert snapshot.last_price == 65050.0
    assert snapshot.position is position
    assert len(snapshot.closed_candles) == 1


def test_signal_service_deduplicates_duplicate_candle_close() -> None:
    source: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    output: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    service = SignalService(
        name="signal",
        strategy=FakeRuntimeStrategy(),
        event_bus=output,
        event_source=source,
        supported_timeframes=("1m",),
    )

    async def run() -> None:
        await service.start()
        event = make_kline_event()
        await source.put(event)
        await source.put(event)
        await asyncio.wait_for(source.join(), timeout=1.0)
        await service.stop()

    asyncio.run(run())

    first = output.get_nowait()
    second = output.get_nowait()
    assert first.meta.event_type == "signal.order_intent"
    assert second.meta.event_type == "signal.suppressed"
    assert isinstance(second.payload, SignalSuppressedPayload)
    assert second.payload.reason == "duplicate_candle_close"
    assert service.get_suppression_counts()["duplicate_candle_close"] == 1
    assert service.get_evaluation_counts()["evaluated"] == 1


def test_signal_service_suppresses_stale_symbol_state() -> None:
    source: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    output: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    stale_state = SymbolState(symbol="BTC/USDT")
    stale_state.mark_stale()
    service = SignalService(
        name="signal",
        strategy=FakeRuntimeStrategy(),
        event_bus=output,
        event_source=source,
        market_state_source=FakeMarketStateSource(stale_state),
        supported_timeframes=("1m",),
    )

    async def run() -> None:
        await _run_service_once(service, source, make_kline_event())

    asyncio.run(run())

    event = output.get_nowait()
    assert event.meta.event_type == "signal.suppressed"
    assert isinstance(event.payload, SignalSuppressedPayload)
    assert event.payload.reason == "stale_symbol_state"
    assert service.get_suppression_counts()["stale_symbol_state"] == 1
    assert service.get_evaluation_counts()["evaluated"] == 1


def test_signal_service_ignores_incomplete_kline_without_suppression_noise() -> None:
    source: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    output: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    service = SignalService(
        name="signal",
        strategy=FakeRuntimeStrategy(),
        event_bus=output,
        event_source=source,
        supported_timeframes=("1m",),
    )

    async def run() -> None:
        await _run_service_once(
            service,
            source,
            make_kline_event(event_id="evt-open", is_closed=False),
        )

    asyncio.run(run())

    assert output.qsize() == 0
    assert service.get_suppression_counts() == {}
    assert service.get_evaluation_counts() == {}


def test_signal_service_records_indicator_cache_invalidation_metrics() -> None:
    source: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    output: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    telemetry = TelemetryService(name="telemetry")
    service = SignalService(
        name="signal",
        strategy=FakeRuntimeStrategy(),
        event_bus=output,
        event_source=source,
        telemetry_service=telemetry,
        supported_timeframes=("1m",),
    )

    async def run() -> None:
        await service.start()
        await source.put(make_kline_event())
        await source.put(
            make_kline_event(
                event_id="evt-gap",
                open_time=datetime(2026, 3, 22, 9, 3, tzinfo=timezone.utc),
                close_time=datetime(2026, 3, 22, 9, 4, tzinfo=timezone.utc),
            )
        )
        await asyncio.wait_for(source.join(), timeout=1.0)
        await service.stop()

    asyncio.run(run())

    assert telemetry.get_metric_count("indicator_cache_invalidated") == 1
    assert telemetry.get_metric_count("indicator_cache_invalidated.gap") == 1
    assert telemetry.get_metric_count("indicator_cache_invalidated.gap.1m") == 1


def test_signal_service_isolates_strategy_exceptions() -> None:
    source: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    output: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    service = SignalService(
        name="signal",
        strategy=FakeRuntimeStrategy(mode="raise"),
        event_bus=output,
        event_source=source,
        supported_timeframes=("1m",),
    )

    async def run() -> None:
        await _run_service_once(service, source, make_kline_event())

    asyncio.run(run())

    event = output.get_nowait()
    assert event.meta.event_type == "signal.suppressed"
    assert isinstance(event.payload, SignalSuppressedPayload)
    assert event.payload.reason == "strategy_exception"
    assert service.get_exception_counts()["strategy_exception"] == 1
    assert service.get_suppression_counts()["strategy_exception"] == 1
