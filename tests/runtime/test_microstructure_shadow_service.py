"""Tests for the live paper-shadow microstructure service."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from domain.events import (
    DepthSnapshotPayload,
    EventMeta,
    MicrostructureDecisionPayload,
    RuntimeEvent,
    TradePayload,
)
from ports.exchange import DepthStreamGateway
from runtime.config import RuntimeConfig
from services.microstructure_shadow_service import MicrostructureShadowService
from strategies.microstructure.paper_shadow_strategy import (
    PaperShadowMicrostructureStrategy,
)


class FakeDepthGateway:
    """Records subscriptions and lets tests drive registered callbacks."""

    def __init__(self, *, fail_subscriptions: bool = False) -> None:
        self.callbacks: dict[str, list] = {}
        self.depth_subscriptions: list[tuple[str, int]] = []
        self.trade_subscriptions: list[str] = []
        self.fail_subscriptions = fail_subscriptions

    def register_callback(self, event_type: str, callback) -> None:
        self.callbacks.setdefault(event_type, []).append(callback)

    async def subscribe_depth(self, symbol: str, depth: int = 10) -> None:
        if self.fail_subscriptions:
            raise RuntimeError("stream unavailable")
        self.depth_subscriptions.append((symbol, depth))

    async def subscribe_trade(self, symbol: str) -> None:
        if self.fail_subscriptions:
            raise RuntimeError("stream unavailable")
        self.trade_subscriptions.append(symbol)

    async def emit(self, event_type: str, event: RuntimeEvent) -> None:
        for callback in self.callbacks.get(event_type, ()):
            await callback(event)


def _runtime_config(**overrides) -> RuntimeConfig:
    defaults = dict(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        runtime_mode="runtime_primary",
    )
    defaults.update(overrides)
    return RuntimeConfig(**defaults)


def _depth_event(
    symbol: str = "BTCUSDT",
    *,
    bids=((100.0, 5.0), (99.5, 4.0)),
    asks=((100.1, 1.0), (100.6, 1.0)),
    sequence: int = 1,
) -> RuntimeEvent:
    return RuntimeEvent(
        meta=EventMeta(
            event_id=uuid4().hex,
            event_type="market.depth",
            source="test.depth",
            symbol=symbol,
            ts_exchange=None,
            ts_received=datetime.now(timezone.utc),
        ),
        payload=DepthSnapshotPayload(
            bids=tuple(bids), asks=tuple(asks), sequence=sequence
        ),
    )


def _trade_event(
    symbol: str = "BTCUSDT", *, side: str = "buy", quantity: float = 2.0
) -> RuntimeEvent:
    return RuntimeEvent(
        meta=EventMeta(
            event_id=uuid4().hex,
            event_type="market.trade",
            source="test.trade",
            symbol=symbol,
            ts_exchange=None,
            ts_received=datetime.now(timezone.utc),
        ),
        payload=TradePayload(price=100.0, quantity=quantity, side=side),
    )


def _build_service(
    gateway: FakeDepthGateway,
    *,
    event_bus: asyncio.Queue | None = None,
    strategy: PaperShadowMicrostructureStrategy | None = None,
) -> MicrostructureShadowService:
    return MicrostructureShadowService(
        name="microstructure-shadow",
        gateway=gateway,
        runtime_config=_runtime_config(),
        strategy=strategy or PaperShadowMicrostructureStrategy(),
        event_bus=event_bus,
    )


def test_fake_gateway_satisfies_depth_stream_protocol() -> None:
    assert isinstance(FakeDepthGateway(), DepthStreamGateway)


@pytest.mark.asyncio
async def test_start_registers_callbacks_and_subscribes_streams() -> None:
    gateway = FakeDepthGateway()
    service = _build_service(gateway)

    await service.start()

    assert "market.depth" in gateway.callbacks
    assert "market.trade" in gateway.callbacks
    assert gateway.depth_subscriptions == [("BTCUSDT", 10)]
    assert gateway.trade_subscriptions == ["BTCUSDT"]

    await service.stop()


@pytest.mark.asyncio
async def test_subscribe_failure_does_not_abort_startup() -> None:
    gateway = FakeDepthGateway(fail_subscriptions=True)
    service = _build_service(gateway)

    await service.start()

    assert service.is_running
    assert "BTC/USDT" in service.get_status()["subscribe_failures"]

    await service.stop()


@pytest.mark.asyncio
async def test_depth_event_updates_local_order_book() -> None:
    gateway = FakeDepthGateway()
    service = _build_service(gateway)
    await service.start()

    await gateway.emit("market.depth", _depth_event(sequence=42))

    snapshot = service.order_books.get_snapshot("BTC/USDT")
    assert snapshot is not None
    assert snapshot.sequence == 42
    assert snapshot.best_bid is not None and snapshot.best_bid.price == 100.0
    assert snapshot.best_ask is not None and snapshot.best_ask.price == 100.1

    await service.stop()


@pytest.mark.asyncio
async def test_bullish_book_emits_buy_decision_with_latency() -> None:
    gateway = FakeDepthGateway()
    event_bus: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    service = _build_service(gateway, event_bus=event_bus)
    await service.start()

    for _ in range(5):
        await gateway.emit("market.trade", _trade_event(side="buy"))
    # Heavy bid-side depth with a tight spread → bullish imbalance decision.
    await gateway.emit(
        "market.depth",
        _depth_event(
            bids=((100.0, 50.0), (99.9, 40.0)),
            asks=((100.01, 1.0), (100.05, 1.0)),
            sequence=7,
        ),
    )

    status = service.get_status()
    assert status["evaluations"] == 1
    assert status["decision_counts"] == {"buy": 1}
    assert status["decision_latency"]["count"] == 1
    assert status["decision_latency"]["last_ms"] is not None

    event = event_bus.get_nowait()
    assert event.meta.event_type == "microstructure.decision"
    payload = event.payload
    assert isinstance(payload, MicrostructureDecisionPayload)
    assert payload.symbol == "BTC/USDT"
    assert payload.action == "buy"
    assert payload.book_sequence == 7
    assert payload.decision_latency_ms >= 0.0

    decisions = service.get_recent_decisions()
    assert len(decisions) == 1
    assert decisions[0]["action"] == "buy"

    await service.stop()


@pytest.mark.asyncio
async def test_balanced_book_emits_no_decision() -> None:
    gateway = FakeDepthGateway()
    event_bus: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    service = _build_service(gateway, event_bus=event_bus)
    await service.start()

    await gateway.emit(
        "market.depth",
        _depth_event(
            bids=((100.0, 10.0), (99.9, 10.0)),
            asks=((100.01, 10.0), (100.05, 10.0)),
        ),
    )

    assert service.get_status()["evaluations"] == 1
    assert service.get_status()["decision_counts"] == {}
    assert event_bus.empty()

    await service.stop()


@pytest.mark.asyncio
async def test_trade_pressure_feeds_feature_vector() -> None:
    gateway = FakeDepthGateway()

    captured: list = []

    class CapturingStrategy(PaperShadowMicrostructureStrategy):
        def evaluate(self, snapshot, features=None, *, decision_latency_ms=0.0):
            captured.append(features)
            return super().evaluate(
                snapshot, features, decision_latency_ms=decision_latency_ms
            )

    service = _build_service(gateway, strategy=CapturingStrategy())
    await service.start()

    await gateway.emit("market.trade", _trade_event(side="buy", quantity=3.0))
    await gateway.emit("market.trade", _trade_event(side="sell", quantity=1.0))
    await gateway.emit("market.depth", _depth_event())

    assert len(captured) == 1
    features = captured[0]
    assert features.trade_count == 2
    assert features.buy_volume == 3.0
    assert features.sell_volume == 1.0
    assert features.trade_pressure == pytest.approx(0.5)

    await service.stop()


def test_invalid_depth_levels_rejected() -> None:
    with pytest.raises(ValueError, match="depth_levels"):
        MicrostructureShadowService(
            name="microstructure-shadow",
            gateway=FakeDepthGateway(),
            runtime_config=_runtime_config(),
            strategy=PaperShadowMicrostructureStrategy(),
            depth_levels=7,
        )
