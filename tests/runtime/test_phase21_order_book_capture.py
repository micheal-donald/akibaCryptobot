from __future__ import annotations

from datetime import datetime, timezone

from backtesting.replay_engine import ReplayEngine
from runtime.depth_capture import DepthCaptureBuffer
from services.order_book_service import OrderBookService
from strategies.microstructure.paper_shadow_strategy import (
    PaperShadowMicrostructureStrategy,
)


def _build_snapshot(service: OrderBookService, *, sequence: int):
    now = datetime(2026, 4, 9, 8, 0, tzinfo=timezone.utc)
    return service.replace_snapshot(
        "BTC/USDT",
        bids=[(100.0, 5.0), (99.5, 2.0)],
        asks=[(100.5, 1.0), (101.0, 1.0)],
        sequence=sequence,
        exchange_timestamp=now,
        received_at=now,
    )


def test_phase21_capture_replay_produces_fill_quality_report(tmp_path) -> None:
    service = OrderBookService(name="phase21-order-book")
    capture = DepthCaptureBuffer(max_records=8)
    snapshot = _build_snapshot(service, sequence=1)
    captured_at = datetime(2026, 4, 9, 8, 0, 5, tzinfo=timezone.utc)

    capture.capture_trade(
        event_id="trade-1",
        event_type="market.trade",
        source="binance.ws.trade",
        symbol="BTC/USDT",
        side="buy",
        price=100.1,
        quantity=2.0,
        sequence=1,
        ts_exchange=captured_at,
        ts_received=captured_at,
        captured_at=captured_at,
    )
    capture.capture_snapshot(
        snapshot,
        event_id="depth-1",
        event_type="market.depth_snapshot",
        source="binance.ws.depth",
        captured_at=captured_at,
    )

    capture_path = capture.write_jsonl(tmp_path / "capture.jsonl")
    engine = ReplayEngine(
        PaperShadowMicrostructureStrategy(
            min_abs_imbalance=0.1,
            min_trade_pressure=0.0,
            min_confidence=0.3,
            max_spread_bps=60.0,
        ),
        decision_latency_ms=12.5,
        submit_latency_ms=4.0,
    )

    report = engine.run(capture_path)

    assert report.capture_health.total_records == 2
    assert report.capture_health.trade_records == 1
    assert report.feature_rows == 1
    assert report.decision_count == 1
    assert report.fill_count == 1
    assert report.buy_count == 1
    assert report.average_decision_latency_ms == 12.5
    assert report.average_submit_latency_ms == 4.0
    assert report.average_fill_quality_bps > 0
    assert report.average_spread_capture_bps > 0
    assert report.decisions[0].action == "buy"
    assert report.decisions[0].execution_style == "passive"
    assert report.fills[0].fill_price == 100.0


def test_phase21_capture_report_marks_sequence_gaps(tmp_path) -> None:
    service = OrderBookService(name="phase21-gap-check")
    capture = DepthCaptureBuffer(max_records=8)
    captured_at = datetime(2026, 4, 9, 8, 5, tzinfo=timezone.utc)

    first_snapshot = _build_snapshot(service, sequence=1)
    second_snapshot = _build_snapshot(service, sequence=3)
    capture.capture_snapshot(
        first_snapshot,
        event_id="depth-1",
        event_type="market.depth_snapshot",
        source="binance.ws.depth",
        captured_at=captured_at,
    )
    capture.capture_snapshot(
        second_snapshot,
        event_id="depth-2",
        event_type="market.depth_snapshot",
        source="binance.ws.depth",
        captured_at=captured_at,
    )

    report = ReplayEngine(
        PaperShadowMicrostructureStrategy(
            min_abs_imbalance=0.1,
            min_trade_pressure=0.0,
            min_confidence=0.3,
            max_spread_bps=60.0,
        )
    ).run(capture.write_jsonl(tmp_path / "gap-capture.jsonl"))

    assert report.capture_health.depth_records == 2
    assert report.capture_health.gap_snapshots == 1
    assert report.capture_health.sequence_issues == 1
    assert report.feature_rows == 2
    assert report.as_dict()["capture_health"]["gap_snapshots"] == 1
