from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.routes.performance import get_low_latency_performance
from indicators.microstructure_features import MicrostructureFeatureVector
from runtime.view_models import ProjectionSource, RuntimeProjection
from services.order_book_service import OrderBookService
from strategies.microstructure.paper_shadow_strategy import (
    PaperShadowMicrostructureStrategy,
)


NOW = datetime(2026, 4, 9, 8, 10, tzinfo=timezone.utc)


def _make_snapshot():
    service = OrderBookService(name="phase21-shadow")
    return service.replace_snapshot(
        "BTC/USDT",
        bids=[(100.0, 4.0), (99.5, 1.0)],
        asks=[(100.5, 1.0), (101.0, 1.0)],
        sequence=11,
        exchange_timestamp=NOW,
        received_at=NOW,
    )


def _make_features(*, imbalance: float, pressure: float, spread_bps: float = 20.0):
    return MicrostructureFeatureVector(
        event_id="evt-21",
        symbol="BTC/USDT",
        sequence=11,
        spread=0.5,
        spread_bps=spread_bps,
        depth_imbalance=imbalance,
        trade_pressure=pressure,
        trade_count=3,
        buy_volume=4.0,
        sell_volume=1.0,
    )


def test_phase21_shadow_strategy_generates_buy_signal_from_bullish_imbalance():
    strategy = PaperShadowMicrostructureStrategy(
        min_abs_imbalance=0.1,
        min_trade_pressure=0.0,
        min_confidence=0.3,
        max_spread_bps=40.0,
    )

    decision = strategy.evaluate(
        _make_snapshot(),
        _make_features(imbalance=0.6, pressure=0.2, spread_bps=18.0),
        decision_latency_ms=8.0,
    )

    assert decision is not None
    assert decision.action == "buy"
    assert decision.execution_style == "passive"
    assert decision.confidence >= 0.3
    assert decision.reason == "bullish_imbalance"


def test_phase21_shadow_strategy_holds_on_wide_spread():
    strategy = PaperShadowMicrostructureStrategy(
        min_abs_imbalance=0.1,
        min_trade_pressure=0.0,
        min_confidence=0.3,
        max_spread_bps=10.0,
    )

    decision = strategy.evaluate(
        _make_snapshot(),
        _make_features(imbalance=0.8, pressure=0.2, spread_bps=25.0),
        decision_latency_ms=4.0,
    )

    assert decision is None


@pytest.mark.asyncio
async def test_phase21_low_latency_performance_route_uses_runtime_telemetry_fallback():
    class FakeBot:
        def get_runtime_projection(self):
            return RuntimeProjection(
                source=ProjectionSource.RUNTIME_PROJECTION,
                runtime_mode="runtime_primary",
                runtime_enabled=True,
                runtime_is_running=True,
                runtime_is_healthy=True,
                runtime_owns_entries=True,
                runtime_owns_exits=True,
                active_position_count=1,
                protected_position_count=0,
                pending_exit_count=0,
                open_order_count=0,
                rejected_order_count=0,
                positions=(),
                orders=(),
            )

        def get_runtime_telemetry_info(self):
            return {
                "total_events": 1,
                "event_counts": {"market.depth_snapshot": 1},
                "metric_counts": {},
                "queue_depths": {},
                "last_seen_at": NOW.isoformat(),
                "latency": {
                    "event_age_ms": {
                        "count": 1,
                        "total_ms": 8.0,
                        "average_ms": 8.0,
                        "min_ms": 8.0,
                        "max_ms": 8.0,
                        "last_ms": 8.0,
                    },
                    "exchange_age_ms": {
                        "count": 1,
                        "total_ms": 3.0,
                        "average_ms": 3.0,
                        "min_ms": 3.0,
                        "max_ms": 3.0,
                        "last_ms": 3.0,
                    },
                    "queue_lag_ms": {},
                },
            }

    payload = await get_low_latency_performance(bot=FakeBot())

    assert payload["available"] is False
    assert payload["runtime_mode"] == "runtime_primary"
    assert payload["decision_latency_ms"] == 8.0
    assert payload["submit_latency_ms"] is None
    assert payload["fill_quality_bps"] is None
    assert payload["capture_health"] is None
