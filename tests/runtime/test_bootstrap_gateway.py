from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from adapters.binance.bootstrap_gateway import BinanceBootstrapGateway
from domain.models import Candle


class FakeOhlcvClient:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str, int]] = []

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
    ):
        self.calls.append((symbol, timeframe, limit))
        return self.payload


def test_bootstrap_gateway_normalizes_dataframe_ohlcv() -> None:
    timestamps = pd.to_datetime(
        ["2026-03-21T12:00:00Z", "2026-03-21T12:01:00Z"], utc=True
    )
    frame = pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.5, 100.5],
            "close": [100.5, 101.5],
            "volume": [25.0, 26.0],
        },
        index=timestamps,
    )
    client = FakeOhlcvClient(frame)
    gateway = BinanceBootstrapGateway(client=client)

    async def run() -> list[Candle]:
        return await gateway.fetch_ohlcv("BTC/USDT", "1m", 2)

    import asyncio

    candles = asyncio.run(run())

    assert client.calls == [("BTC/USDT", "1m", 2)]
    assert len(candles) == 2
    assert candles[0].open_time == timestamps[0].to_pydatetime()
    assert candles[0].close_time == timestamps[0].to_pydatetime()
    assert candles[0].close == 100.5
    assert candles[1].volume == 26.0


def test_bootstrap_gateway_normalizes_sequence_ohlcv() -> None:
    payload = [
        {
            "timestamp": 1_743_480_000_000,
            "close_time": 1_743_480_060_000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.5,
            "close": 100.5,
            "volume": 25.0,
        }
    ]
    client = FakeOhlcvClient(payload)
    gateway = BinanceBootstrapGateway(client=client)

    async def run() -> list[Candle]:
        return await gateway.fetch_ohlcv("ETH/USDT", "15m", 1)

    import asyncio

    candles = asyncio.run(run())

    expected_open = datetime.fromtimestamp(1_743_480_000_000 / 1000, tz=timezone.utc)
    expected_close = datetime.fromtimestamp(1_743_480_060_000 / 1000, tz=timezone.utc)

    assert client.calls == [("ETH/USDT", "15m", 1)]
    assert len(candles) == 1
    assert candles[0].open_time == expected_open
    assert candles[0].close_time == expected_close
    assert candles[0].close == 100.5


def test_bootstrap_gateway_accepts_exchange_symbol_input() -> None:
    payload = [
        {
            "timestamp": 1_743_480_000_000,
            "close_time": 1_743_480_060_000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.5,
            "close": 100.5,
            "volume": 25.0,
        }
    ]
    client = FakeOhlcvClient(payload)
    gateway = BinanceBootstrapGateway(client=client)

    async def run() -> list[Candle]:
        return await gateway.fetch_ohlcv("BTCUSDT", "1m", 1)

    import asyncio

    candles = asyncio.run(run())

    assert client.calls == [("BTC/USDT", "1m", 1)]
    assert len(candles) == 1
