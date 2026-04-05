from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from indicators.microstructure_features import (
    MicrostructureFeatureVector,
    depth_imbalance,
    feature_vector_from_replay,
    spread_bps_from_snapshot,
    spread_from_snapshot,
    trade_pressure,
)
from runtime.depth_capture import DepthCaptureBuffer
from runtime.depth_replay import RuntimeDepthReplayHarness
from services.order_book_service import OrderBookService


def _build_replay_steps():
    buffer = DepthCaptureBuffer(max_records=8)
    base_time = datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)
    service = OrderBookService(name="order-book")

    first_snapshot = service.replace_snapshot(
        "BTCUSDT",
        bids=[(100.0, 2.0), (99.5, 1.0)],
        asks=[(100.5, 1.0), (101.0, 3.0)],
        sequence=10,
        exchange_timestamp=base_time,
        received_at=base_time,
    )
    buffer.capture_snapshot(
        first_snapshot,
        event_id="depth-1",
        event_type="order_book.snapshot",
        source="runtime.order_book",
        captured_at=base_time + timedelta(milliseconds=10),
    )

    buffer.capture_trade(
        event_id="trade-1",
        event_type="market.trade",
        source="binance.ws.trade",
        symbol="BTCUSDT",
        side="buy",
        price=100.2,
        quantity=2.0,
        sequence=11,
        ts_exchange=base_time + timedelta(milliseconds=20),
        ts_received=base_time + timedelta(milliseconds=30),
        captured_at=base_time + timedelta(milliseconds=40),
    )
    buffer.capture_trade(
        event_id="trade-2",
        event_type="market.trade",
        source="binance.ws.trade",
        symbol="BTCUSDT",
        side="sell",
        price=100.1,
        quantity=1.0,
        sequence=12,
        ts_exchange=base_time + timedelta(milliseconds=50),
        ts_received=base_time + timedelta(milliseconds=60),
        captured_at=base_time + timedelta(milliseconds=70),
    )
    buffer.capture_trade(
        event_id="trade-3",
        event_type="market.trade",
        source="binance.ws.trade",
        symbol="BTCUSDT",
        side="buy",
        price=100.4,
        quantity=3.0,
        sequence=13,
        ts_exchange=base_time + timedelta(milliseconds=80),
        ts_received=base_time + timedelta(milliseconds=90),
        captured_at=base_time + timedelta(milliseconds=100),
    )

    second_snapshot = service.replace_snapshot(
        "BTCUSDT",
        bids=[(100.1, 3.0), (99.6, 2.0)],
        asks=[(100.6, 1.0), (101.1, 2.0)],
        sequence=14,
        exchange_timestamp=base_time + timedelta(seconds=1),
        received_at=base_time + timedelta(seconds=1),
    )
    buffer.capture_snapshot(
        second_snapshot,
        event_id="depth-2",
        event_type="order_book.snapshot",
        source="runtime.order_book",
        captured_at=base_time + timedelta(seconds=1, milliseconds=10),
    )

    return RuntimeDepthReplayHarness.from_jsonl(buffer.to_json_lines())


def test_microstructure_helpers_compute_spread_and_imbalance() -> None:
    service = OrderBookService(name="order-book")
    snapshot = service.replace_snapshot(
        "ETHUSDT",
        bids=[(1500.0, 2.0), (1499.5, 1.0)],
        asks=[(1500.5, 1.0), (1501.0, 3.0)],
        sequence=7,
    )

    assert spread_from_snapshot(snapshot) == pytest.approx(0.5)
    assert spread_bps_from_snapshot(snapshot) == pytest.approx(
        snapshot.spread / snapshot.mid_price * 10_000
    )
    assert depth_imbalance(snapshot, depth=1) == pytest.approx(1 / 3)
    assert depth_imbalance(snapshot, depth=2) == pytest.approx(-1 / 7)


def test_trade_pressure_and_replay_features_use_recent_trade_flow() -> None:
    harness = _build_replay_steps()
    steps = tuple(harness.iter_steps())

    assert trade_pressure(steps, symbol="BTCUSDT", window=3) == pytest.approx(2 / 3)

    vectors = feature_vector_from_replay(steps, depth_window=2, trade_window=3)
    assert len(vectors) == 2
    assert isinstance(vectors[0], MicrostructureFeatureVector)

    first, second = vectors
    assert first.symbol == "BTCUSDT"
    assert first.spread == pytest.approx(0.5)
    assert first.depth_imbalance == pytest.approx(-1 / 7)
    assert first.trade_count == 0
    assert first.trade_pressure is None

    assert second.sequence == 14
    assert second.spread == pytest.approx(0.5)
    assert second.spread_bps == pytest.approx(0.5 / 100.35 * 10_000)
    assert second.depth_imbalance == pytest.approx(1 / 4)
    assert second.trade_count == 3
    assert second.buy_volume == pytest.approx(5.0)
    assert second.sell_volume == pytest.approx(1.0)
    assert second.trade_pressure == pytest.approx(2 / 3)


def test_trade_pressure_returns_none_without_trade_flow() -> None:
    service = OrderBookService(name="order-book")
    snapshot = service.replace_snapshot(
        "BNBUSDT",
        bids=[(600.0, 1.0)],
        asks=[(600.5, 1.0)],
        sequence=1,
    )

    assert spread_from_snapshot(snapshot) == pytest.approx(0.5)
    assert trade_pressure((), symbol="BNBUSDT") is None
    assert feature_vector_from_replay(()) == ()
