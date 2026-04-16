from __future__ import annotations

from datetime import datetime, timezone

import pytest

from adapters.binance.ws_gateway import BinanceWebSocketGateway
from domain.events import BookTickerPayload, KlinePayload, RuntimeEvent


class FakeWebSocketManager:
    def __init__(self) -> None:
        self.callbacks: dict[str, object] = {}
        self.calls: list[object] = []
        self.is_connected = False

    async def connect(self) -> None:
        self.calls.append("connect")
        self.is_connected = True

    async def disconnect(self) -> None:
        self.calls.append("disconnect")
        self.is_connected = False

    def register_callback(self, event_type: str, callback: object) -> None:
        self.callbacks[event_type] = callback

    async def subscribe_book_ticker(self, symbol: str) -> None:
        self.calls.append(("book_ticker", symbol))

    async def subscribe_kline(self, symbol: str, interval: str = "1m") -> None:
        self.calls.append(("kline", symbol, interval))


def test_gateway_connects_registers_bridges_and_proxies_calls() -> None:
    manager = FakeWebSocketManager()
    gateway = BinanceWebSocketGateway(websocket_manager=manager)

    async def run() -> None:
        await gateway.connect()
        await gateway.subscribe_book_ticker("BTCUSDT")
        await gateway.subscribe_kline("BTCUSDT", "1m")
        await gateway.disconnect()

    import asyncio

    asyncio.run(run())

    assert manager.calls == [
        "connect",
        ("book_ticker", "BTCUSDT"),
        ("kline", "BTCUSDT", "1m"),
        "disconnect",
    ]
    assert "book_ticker" in manager.callbacks
    assert "kline" in manager.callbacks


def test_gateway_normalizes_book_ticker_events() -> None:
    manager = FakeWebSocketManager()
    gateway = BinanceWebSocketGateway(websocket_manager=manager)
    events: list[RuntimeEvent] = []
    gateway.register_callback("market.tick", events.append)

    ts = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)

    async def run() -> None:
        await gateway.connect()
        await manager.callbacks["book_ticker"](
            {
                "symbol": "BTCUSDT",
                "bid_price": 100.0,
                "bid_qty": 1.5,
                "ask_price": 100.2,
                "ask_qty": 2.0,
                "timestamp": ts,
            }
        )

    import asyncio

    asyncio.run(run())

    assert len(events) == 1
    assert events[0].meta.event_type == "market.tick"
    assert events[0].meta.source == "binance.ws.book_ticker"
    assert events[0].meta.symbol == "BTCUSDT"
    assert events[0].meta.ts_exchange == ts
    assert isinstance(events[0].payload, BookTickerPayload)
    assert events[0].payload.ask_price == 100.2


def test_gateway_normalizes_raw_binance_book_ticker_payloads() -> None:
    manager = FakeWebSocketManager()
    gateway = BinanceWebSocketGateway(websocket_manager=manager)
    events: list[RuntimeEvent] = []
    gateway.register_callback("market.tick", events.append)

    expected_ts = datetime.fromtimestamp(1_743_480_000_000 / 1000, tz=timezone.utc)

    async def run() -> None:
        await gateway.connect()
        await manager.callbacks["book_ticker"](
            {
                "s": "BTCUSDT",
                "b": "100.0",
                "B": "1.5",
                "a": "100.2",
                "A": "2.0",
                "E": 1_743_480_000_000,
            }
        )

    import asyncio

    asyncio.run(run())

    assert len(events) == 1
    assert events[0].meta.symbol == "BTCUSDT"
    assert events[0].meta.ts_exchange == expected_ts
    assert isinstance(events[0].payload, BookTickerPayload)
    assert events[0].payload.bid_price == 100.0
    assert events[0].payload.ask_qty == 2.0


def test_gateway_routes_open_and_closed_kline_events() -> None:
    manager = FakeWebSocketManager()
    gateway = BinanceWebSocketGateway(websocket_manager=manager)
    open_events: list[RuntimeEvent] = []
    closed_events: list[RuntimeEvent] = []
    gateway.register_callback("market.kline", open_events.append)
    gateway.register_callback("market.kline_closed", closed_events.append)

    open_time_ms = 1_743_480_060_000
    close_time_ms = 1_743_480_120_000
    event_time_ms = 1_743_480_120_500
    expected_open_time = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
    expected_close_time = datetime.fromtimestamp(close_time_ms / 1000, tz=timezone.utc)
    expected_event_time = datetime.fromtimestamp(event_time_ms / 1000, tz=timezone.utc)

    async def run() -> None:
        await gateway.connect()
        await manager.callbacks["kline"](
            {
                "symbol": "BTCUSDT",
                "interval": "1m",
                "open_time": open_time_ms,
                "close_time": close_time_ms,
                "open": 100.0,
                "high": 101.0,
                "low": 99.5,
                "close": 100.5,
                "volume": 25.0,
                "is_closed": False,
                "timestamp": event_time_ms,
            }
        )
        await manager.callbacks["kline"](
            {
                "symbol": "BTCUSDT",
                "interval": "1m",
                "open_time": open_time_ms,
                "close_time": close_time_ms,
                "open": 100.0,
                "high": 101.0,
                "low": 99.5,
                "close": 100.5,
                "volume": 25.0,
                "is_closed": True,
                "timestamp": event_time_ms,
            }
        )

    import asyncio

    asyncio.run(run())

    assert len(open_events) == 1
    assert len(closed_events) == 1
    assert open_events[0].meta.event_type == "market.kline"
    assert closed_events[0].meta.event_type == "market.kline_closed"
    assert isinstance(open_events[0].payload, KlinePayload)
    assert open_events[0].payload.is_closed is False
    assert closed_events[0].payload.is_closed is True
    assert open_events[0].payload.open_time == expected_open_time
    assert open_events[0].payload.close_time == expected_close_time
    assert open_events[0].meta.ts_exchange == expected_event_time
    assert closed_events[0].meta.ts_exchange == expected_event_time


def test_gateway_normalizes_raw_binance_nested_kline_payloads() -> None:
    manager = FakeWebSocketManager()
    gateway = BinanceWebSocketGateway(websocket_manager=manager)
    closed_events: list[RuntimeEvent] = []
    gateway.register_callback("market.kline_closed", closed_events.append)

    open_time_ms = 1_743_480_060_000
    close_time_ms = 1_743_480_120_000
    event_time_ms = 1_743_480_120_500
    expected_open_time = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
    expected_close_time = datetime.fromtimestamp(close_time_ms / 1000, tz=timezone.utc)
    expected_event_time = datetime.fromtimestamp(event_time_ms / 1000, tz=timezone.utc)

    async def run() -> None:
        await gateway.connect()
        await manager.callbacks["kline"](
            {
                "e": "kline",
                "E": event_time_ms,
                "s": "BTCUSDT",
                "k": {
                    "t": open_time_ms,
                    "T": close_time_ms,
                    "i": "1m",
                    "o": "100.0",
                    "h": "101.0",
                    "l": "99.5",
                    "c": "100.5",
                    "v": "25.0",
                    "x": True,
                },
            }
        )

    import asyncio

    asyncio.run(run())

    assert len(closed_events) == 1
    assert closed_events[0].meta.symbol == "BTCUSDT"
    assert closed_events[0].meta.ts_exchange == expected_event_time
    assert isinstance(closed_events[0].payload, KlinePayload)
    assert closed_events[0].payload.interval == "1m"
    assert closed_events[0].payload.open_time == expected_open_time
    assert closed_events[0].payload.close_time == expected_close_time
    assert closed_events[0].payload.is_closed is True


def test_gateway_rejects_unsupported_event_registration() -> None:
    gateway = BinanceWebSocketGateway(websocket_manager=FakeWebSocketManager())

    with pytest.raises(ValueError, match="unsupported market-data event type"):
        gateway.register_callback("runtime.shutdown", lambda event: None)  # type: ignore[arg-type]


def test_coerce_datetime_forces_naive_to_utc() -> None:
    """Regression: ws_gateway used to forward naive datetimes unchanged,
    which leaked into EventMeta.ts_exchange and broke tz-aware subtraction
    in telemetry. It now mirrors user_data_gateway — naive inputs are
    interpreted as UTC, aware inputs are normalized to UTC."""

    naive = datetime(2026, 4, 5, 10, 0)
    coerced = BinanceWebSocketGateway._coerce_datetime(naive)
    assert coerced is not None
    assert coerced.tzinfo == timezone.utc
    assert coerced == naive.replace(tzinfo=timezone.utc)

    aware = datetime(2026, 4, 5, 10, 0, tzinfo=timezone.utc)
    assert BinanceWebSocketGateway._coerce_datetime(aware) == aware

    # Integers still flow through the epoch branch — sanity check that the
    # new isinstance(datetime) branch didn't swallow numeric inputs.
    ms_epoch = 1_770_000_000_000
    assert BinanceWebSocketGateway._coerce_datetime(ms_epoch) == datetime.fromtimestamp(
        ms_epoch / 1000.0, tz=timezone.utc
    )
