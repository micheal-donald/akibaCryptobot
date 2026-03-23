from __future__ import annotations

from datetime import datetime, timedelta, timezone

from domain.models import Candle
from strategies.indicator_cache import IndicatorWindowCache


def make_candle(
    minute: int,
    *,
    close: float | None = None,
) -> Candle:
    open_time = datetime(2026, 3, 22, 9, minute, tzinfo=timezone.utc)
    close_time = open_time + timedelta(minutes=1)
    price = close if close is not None else 65000.0 + minute
    return Candle(
        open_time=open_time,
        close_time=close_time,
        open=price - 10.0,
        high=price + 10.0,
        low=price - 15.0,
        close=price,
        volume=12.5 + minute,
    )


def test_indicator_window_cache_bounded_and_symbol_wide_invalidation() -> None:
    cache = IndicatorWindowCache(max_window_size=3)

    for minute in range(4):
        cache.append_candle("BTC/USDT", "1m", make_candle(minute))

    cache.append_candle("BTC/USDT", "5m", make_candle(0, close=66000.0))
    cache.append_candle("BTC/USDT", "5m", make_candle(1, close=66010.0))

    assert [c.close for c in cache.get_window("BTC/USDT", "1m")] == [
        65001.0,
        65002.0,
        65003.0,
    ]
    assert [c.close for c in cache.get_window("BTC/USDT", "5m")] == [
        66000.0,
        66010.0,
    ]

    cache.mark_stale("BTC/USDT")

    assert cache.get_window("BTC/USDT", "1m") == ()
    assert cache.get_window("BTC/USDT", "5m") == ()
    assert cache.get_invalidation_reason("BTC/USDT", "1m") == "stale"
    assert cache.get_invalidation_reason("BTC/USDT", "5m") == "stale"

    cache.append_candle("BTC/USDT", "1m", make_candle(4))
    cache.append_candle("BTC/USDT", "5m", make_candle(5, close=66050.0))

    assert [c.close for c in cache.get_window("BTC/USDT", "1m")] == [65004.0]
    assert [c.close for c in cache.get_window("BTC/USDT", "5m")] == [66050.0]
    assert cache.get_invalidation_reason("BTC/USDT", "1m") is None
    assert cache.get_invalidation_reason("BTC/USDT", "5m") is None


def test_indicator_window_cache_invalidates_and_rebuilds_after_gap() -> None:
    cache = IndicatorWindowCache(max_window_size=5)

    cache.append_candle("ETH/USDT", "1m", make_candle(0, close=3200.0))
    cache.append_candle("ETH/USDT", "1m", make_candle(1, close=3205.0))
    cache.append_candle("ETH/USDT", "1m", make_candle(3, close=3210.0))

    assert [c.close for c in cache.get_window("ETH/USDT", "1m")] == [3210.0]
    assert cache.get_last_close_time("ETH/USDT", "1m") == datetime(
        2026, 3, 22, 9, 4, tzinfo=timezone.utc
    )
    assert cache.get_invalidation_reason("ETH/USDT", "1m") is None


def test_indicator_window_cache_explicit_gap_mark_clears_window() -> None:
    cache = IndicatorWindowCache(max_window_size=4)

    cache.append_candle("BTC/USDT", "15m", make_candle(0, close=67000.0))
    cache.append_candle("BTC/USDT", "15m", make_candle(1, close=67010.0))

    cache.mark_gap("BTC/USDT", "15m")

    assert cache.get_window("BTC/USDT", "15m") == ()
    assert cache.get_invalidation_reason("BTC/USDT", "15m") == "gap"

    cache.append_candle("BTC/USDT", "15m", make_candle(2, close=67020.0))

    assert [c.close for c in cache.get_window("BTC/USDT", "15m")] == [67020.0]
    assert cache.get_invalidation_reason("BTC/USDT", "15m") is None
