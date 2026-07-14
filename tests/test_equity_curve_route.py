"""Tests for the /api/equity-curve charting endpoint."""

from datetime import datetime, timedelta, timezone

import pytest

from api.routes.performance import EQUITY_MAX_POINTS, get_equity_curve
from performance.metrics_tracker import PerformanceMetricsTracker


class TrackerBot:
    def __init__(self, tracker):
        self.performance_tracker = tracker


class FallbackBot:
    performance_tracker = None

    def __init__(self, history):
        self.portfolio_value_history = history


def make_tracker(points):
    tracker = PerformanceMetricsTracker(initial_balance=10000.0)
    tracker.equity_curve = list(points)
    return tracker


@pytest.mark.asyncio
async def test_returns_chart_ready_points():
    base = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    tracker = make_tracker(
        [
            (base + timedelta(minutes=5), 10100.0),
            (base, 10000.0),
        ]
    )

    payload = await get_equity_curve(range="all", bot=TrackerBot(tracker))

    assert payload["range"] == "all"
    assert payload["initial_balance"] == 10000.0
    times = [p["time"] for p in payload["data"]]
    assert times == sorted(times)
    assert payload["data"][0] == {"time": int(base.timestamp()), "value": 10000.0}


@pytest.mark.asyncio
async def test_range_windows_points():
    now = datetime.now(timezone.utc)
    tracker = make_tracker(
        [
            (now - timedelta(days=10), 9000.0),
            (now - timedelta(hours=2), 10200.0),
            (now - timedelta(hours=1), 10300.0),
        ]
    )

    payload = await get_equity_curve(range="1d", bot=TrackerBot(tracker))

    values = [p["value"] for p in payload["data"]]
    # The 10-day-old point survives only as the pre-window anchor
    assert values == [9000.0, 10200.0, 10300.0]

    payload_all = await get_equity_curve(range="all", bot=TrackerBot(tracker))
    assert len(payload_all["data"]) == 3


@pytest.mark.asyncio
async def test_large_curve_is_downsampled():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    tracker = make_tracker(
        [(base + timedelta(minutes=i), 10000.0 + i) for i in range(10000)]
    )

    payload = await get_equity_curve(range="all", bot=TrackerBot(tracker))

    assert len(payload["data"]) == EQUITY_MAX_POINTS


@pytest.mark.asyncio
async def test_fallback_history_gets_synthetic_times():
    bot = FallbackBot([10000.0, 10050.0, 10100.0])

    payload = await get_equity_curve(range="all", bot=bot)

    assert payload["initial_balance"] == 10000.0
    times = [p["time"] for p in payload["data"]]
    assert times == sorted(times)
    assert len(payload["data"]) == 3
    assert payload["data"][-1]["value"] == 10100.0


@pytest.mark.asyncio
async def test_empty_curve_returns_empty_data():
    bot = FallbackBot([])

    payload = await get_equity_curve(range="all", bot=bot)

    assert payload["data"] == []
    assert payload["initial_balance"] is None
