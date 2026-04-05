from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from domain.events import BookTickerPayload, EventMeta, KlinePayload, RuntimeEvent
from domain.models import Candle
from runtime.config import RuntimeConfig
from services.market_data_service import MarketDataService
from services.telemetry_service import TelemetryService


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


class FakeBootstrapGateway:
    def __init__(self, payloads: dict[tuple[str, str], list[Candle]]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, str, int]] = []

    async def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        limit: int,
    ) -> list[Candle]:
        self.calls.append((symbol, interval, limit))
        return list(self.payloads.get((symbol, interval), ()))


def build_service(
    *,
    bootstrap_gateway: FakeBootstrapGateway | None = None,
) -> tuple[MarketDataService, FakeMarketDataGateway, asyncio.Queue]:
    gateway = FakeMarketDataGateway()
    queue: asyncio.Queue = asyncio.Queue()
    telemetry = TelemetryService(name="telemetry")
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
        bootstrap_gateway=bootstrap_gateway,
        telemetry_service=telemetry,
    )
    return service, gateway, queue


def _make_candle(
    minute: int,
    close: float,
    *,
    base_time: datetime,
) -> Candle:
    open_time = base_time + timedelta(minutes=minute)
    close_time = open_time + timedelta(minutes=1)
    return Candle(
        open_time=open_time,
        close_time=close_time,
        open=close - 0.5,
        high=close + 0.5,
        low=close - 1.0,
        close=close,
        volume=25.0,
    )


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


def test_market_data_service_bootstraps_history_before_subscribing() -> None:
    base_time = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    bootstrap = FakeBootstrapGateway(
        {
            (
                "BTCUSDT",
                "1m",
            ): [
                _make_candle(0, 100.5, base_time=base_time),
                _make_candle(1, 101.5, base_time=base_time),
            ],
            ("BTCUSDT", "15m"): [_make_candle(0, 110.5, base_time=base_time)],
        }
    )
    service, gateway, _ = build_service(bootstrap_gateway=bootstrap)

    async def run() -> None:
        await service.start()
        await service.stop()

    asyncio.run(run())

    assert bootstrap.calls == [
        ("BTCUSDT", "1m", 16),
        ("BTCUSDT", "15m", 16),
    ]
    assert gateway.calls[0] == "connect"
    assert gateway.calls[1:] == [
        ("book_ticker", "BTCUSDT"),
        ("kline", "BTCUSDT", "1m"),
        ("kline", "BTCUSDT", "15m"),
        "disconnect",
    ]

    state = service.get_symbol_state("BTC/USDT")
    assert state is not None
    assert len(state.candles_1m) == 2
    assert len(state.candle_buffers["15m"]) == 1
    assert state.last_price == 110.5
    assert (
        state.last_kline_by_interval["1m"]
        == _make_candle(
            1,
            101.5,
            base_time=base_time,
        ).close_time
    )
    assert state.is_stale is False


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


def test_market_data_service_keeps_closed_history_closed_only() -> None:
    service, gateway, _ = build_service()
    open_time = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)
    close_time = datetime(2026, 3, 21, 12, 1, tzinfo=timezone.utc)

    async def run() -> None:
        await service.start()
        await gateway.callbacks["market.kline"][0](
            RuntimeEvent(
                meta=EventMeta(
                    event_id="evt-open",
                    event_type="market.kline",
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
                    close=100.4,
                    volume=20.0,
                    is_closed=False,
                ),
            )
        )
        await gateway.callbacks["market.kline_closed"][0](
            RuntimeEvent(
                meta=EventMeta(
                    event_id="evt-closed-1",
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
        await gateway.callbacks["market.kline_closed"][0](
            RuntimeEvent(
                meta=EventMeta(
                    event_id="evt-closed-2",
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
                    high=101.5,
                    low=99.5,
                    close=100.8,
                    volume=30.0,
                    is_closed=True,
                ),
            )
        )

    asyncio.run(run())

    candles = service.get_candles("BTC/USDT", "1m")
    assert len(candles) == 1
    assert candles[0].close == 100.8


def test_market_data_service_records_stale_transition_metric() -> None:
    service, _, _ = build_service()
    stale_now = datetime(2026, 3, 21, 12, 5, tzinfo=timezone.utc)
    state = service._create_symbol_state("BTC/USDT")
    state.last_update_at = stale_now - timedelta(seconds=120)
    service._symbol_states["BTC/USDT"] = state

    stale_symbols = service.refresh_stale_state(now=stale_now)

    assert stale_symbols == ("BTC/USDT",)
    assert service.telemetry_service is not None
    assert service.telemetry_service.get_metric_count("stale_symbol.BTC/USDT") == 1


def test_market_data_service_tracks_multi_interval_candles() -> None:
    service, gateway, _ = build_service()
    one_minute_close = datetime(2026, 3, 21, 12, 1, tzinfo=timezone.utc)
    fifteen_minute_close = datetime(2026, 3, 21, 12, 15, tzinfo=timezone.utc)

    async def run() -> None:
        await service.start()
        await gateway.callbacks["market.kline_closed"][0](
            RuntimeEvent(
                meta=EventMeta(
                    event_id="evt-2a",
                    event_type="market.kline_closed",
                    source="binance.ws.kline.1m",
                    symbol="BTCUSDT",
                    ts_exchange=one_minute_close,
                    ts_received=one_minute_close,
                ),
                payload=KlinePayload(
                    interval="1m",
                    open_time=one_minute_close,
                    close_time=one_minute_close,
                    open=100.0,
                    high=101.0,
                    low=99.5,
                    close=100.5,
                    volume=25.0,
                    is_closed=True,
                ),
            )
        )
        await gateway.callbacks["market.kline_closed"][0](
            RuntimeEvent(
                meta=EventMeta(
                    event_id="evt-2b",
                    event_type="market.kline_closed",
                    source="binance.ws.kline.15m",
                    symbol="BTCUSDT",
                    ts_exchange=fifteen_minute_close,
                    ts_received=fifteen_minute_close,
                ),
                payload=KlinePayload(
                    interval="15m",
                    open_time=fifteen_minute_close,
                    close_time=fifteen_minute_close,
                    open=110.0,
                    high=111.0,
                    low=109.5,
                    close=110.5,
                    volume=30.0,
                    is_closed=True,
                ),
            )
        )

    asyncio.run(run())

    state = service.get_symbol_state("BTC/USDT")
    assert state is not None
    assert service.get_candles("BTC/USDT", "1m")[0].close == 100.5
    assert service.get_candles("BTC/USDT", "15m")[0].close == 110.5
    assert state.candle_buffers["15m"][-1].close == 110.5
    assert state.last_kline_by_interval["15m"] == fifteen_minute_close


def test_market_data_service_returns_read_only_snapshots() -> None:
    service, gateway, _ = build_service()
    ts = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    close_time = ts + timedelta(minutes=1)

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
                    open_time=ts,
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
    snapshot.candles_1m.clear()
    assert service.get_symbol_state("BTC/USDT").best_bid == 100.0
    assert len(service.get_symbol_state("BTC/USDT").candles_1m) == 1
