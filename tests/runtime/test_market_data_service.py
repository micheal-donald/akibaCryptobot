from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from domain.events import BookTickerPayload, EventMeta, KlinePayload, RuntimeEvent
from runtime.config import RuntimeConfig
from services.market_data_service import MarketDataService


class FakeMarketDataGateway:
    def __init__(self) -> None:
        self.callbacks: dict[str, list[object]] = {}
        self.calls: list[object] = []
        self.connected = False

    async def connect(self) -> None:
        self.calls.append("connect")
        self.connected = True

    async def disconnect(self) -> None:
        self.calls.append("disconnect")
        self.connected = False

    def register_callback(self, event_type: str, callback: object) -> None:
        self.callbacks.setdefault(event_type, []).append(callback)

    async def subscribe_book_ticker(self, symbol: str) -> None:
        self.calls.append(("book_ticker", symbol))

    async def subscribe_kline(self, symbol: str, interval: str = "1m") -> None:
        self.calls.append(("kline", symbol, interval))


def build_service() -> tuple[MarketDataService, FakeMarketDataGateway, asyncio.Queue]:
    gateway = FakeMarketDataGateway()
    queue: asyncio.Queue = asyncio.Queue()
    config = RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m", "15m"),
        candle_history_limit=16,
    )
    service = MarketDataService(
        name="market-data",
        gateway=gateway,
        runtime_config=config,
        event_bus=queue,
    )
    return service, gateway, queue


def test_market_data_service_starts_and_subscribes() -> None:
    service, gateway, _ = build_service()

    async def run() -> None:
        await service.start()
        await service.stop()

    asyncio.run(run())

    assert gateway.calls == [
        "connect",
        ("book_ticker", "BTCUSDT"),
        ("kline", "BTCUSDT", "1m"),
        ("kline", "BTCUSDT", "15m"),
        "disconnect",
    ]
    assert set(gateway.callbacks) == {
        "market.tick",
        "market.kline",
        "market.kline_closed",
    }


def test_market_data_service_updates_state_and_publishes_tick_events() -> None:
    service, gateway, queue = build_service()
    ts = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)

    async def run() -> None:
        await service.start()
        await gateway.callbacks["market.tick"][0](
            RuntimeEvent(
                meta=EventMeta(
                    event_id="evt-1",
                    event_type="market.tick",
                    source="binance.ws.book_ticker",
                    symbol="BTCUSDT",
                    ts_exchange=ts,
                    ts_received=ts,
                ),
                payload=BookTickerPayload(
                    bid_price=100.0,
                    bid_qty=1.5,
                    ask_price=100.2,
                    ask_qty=2.0,
                ),
            )
        )

    asyncio.run(run())

    state = service.get_symbol_state("BTC/USDT")
    assert state is not None
    assert state.best_bid == 100.0
    assert state.best_ask == 100.2
    assert state.last_price == pytest.approx(100.1)
    assert state.spread_bps == pytest.approx(19.98001998, rel=1e-9)
    assert queue.qsize() == 1


def test_market_data_service_updates_kline_buffers_and_returns_snapshots() -> None:
    service, gateway, queue = build_service()
    open_time = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)
    close_time = datetime(2026, 3, 21, 12, 1, tzinfo=timezone.utc)

    async def run() -> None:
        await service.start()
        await gateway.callbacks["market.kline_closed"][0](
            RuntimeEvent(
                meta=EventMeta(
                    event_id="evt-2",
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
                    open=100.0,
                    high=101.0,
                    low=99.5,
                    close=100.5,
                    volume=25.0,
                    is_closed=True,
                ),
            )
        )

    asyncio.run(run())

    state = service.get_symbol_state("BTC/USDT")
    assert state is not None
    assert len(state.candles_1m) == 1
    assert state.last_kline_close == close_time
    assert state.last_price == 100.5
    assert service.get_candles("BTC/USDT", "1m")[0].close == 100.5
    assert queue.qsize() == 1


def test_market_data_service_returns_read_only_snapshots() -> None:
    service, gateway, _ = build_service()
    ts = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)

    async def run() -> None:
        await service.start()
        await gateway.callbacks["market.tick"][0](
            RuntimeEvent(
                meta=EventMeta(
                    event_id="evt-3",
                    event_type="market.tick",
                    source="binance.ws.book_ticker",
                    symbol="BTCUSDT",
                    ts_exchange=ts,
                    ts_received=ts,
                ),
                payload=BookTickerPayload(
                    bid_price=100.0,
                    bid_qty=1.0,
                    ask_price=100.2,
                    ask_qty=1.0,
                ),
            )
        )

    asyncio.run(run())

    snapshot = service.get_symbol_state("BTC/USDT")
    assert snapshot is not None
    snapshot.best_bid = 1.0
    assert service.get_symbol_state("BTC/USDT").best_bid == 100.0
