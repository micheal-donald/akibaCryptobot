from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from runtime.depth_capture import DepthCaptureBuffer
from runtime.depth_replay import (
    RuntimeDepthReplayHarness,
    load_depth_replay,
    replay_depth_events,
)
from services.order_book_service import OrderBookService


def _build_capture_buffer() -> tuple[DepthCaptureBuffer, datetime]:
    buffer = DepthCaptureBuffer(max_records=8)
    base_time = datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)

    first = OrderBookService(name="order-book").replace_snapshot(
        "BTCUSDT",
        bids=[(100.0, 1.5), (99.5, 1.0)],
        asks=[(100.5, 1.2), (101.0, 1.1)],
        sequence=10,
        exchange_timestamp=base_time,
        received_at=base_time,
    )
    buffer.capture_snapshot(
        first,
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
        quantity=0.4,
        sequence=11,
        ts_exchange=base_time + timedelta(milliseconds=20),
        ts_received=base_time + timedelta(milliseconds=30),
        captured_at=base_time + timedelta(milliseconds=40),
    )
    second = OrderBookService(name="order-book").replace_snapshot(
        "BTCUSDT",
        bids=[(100.1, 1.7), (99.6, 1.3)],
        asks=[(100.6, 0.9), (101.1, 1.0)],
        sequence=12,
        exchange_timestamp=base_time + timedelta(seconds=1),
        received_at=base_time + timedelta(seconds=1),
    )
    buffer.capture_snapshot(
        second,
        event_id="depth-2",
        event_type="order_book.snapshot",
        source="runtime.order_book",
        captured_at=base_time + timedelta(seconds=1, milliseconds=10),
    )
    return buffer, base_time


def test_depth_replay_loads_records_in_order(tmp_path) -> None:
    buffer, _ = _build_capture_buffer()
    capture_path = buffer.write_jsonl(tmp_path / "depth.jsonl")

    harness = load_depth_replay(capture_path)
    records = tuple(harness.iter_records())

    assert [record.event_id for record in records] == [
        "depth-1",
        "trade-1",
        "depth-2",
    ]
    assert [record.payload_type for record in records] == [
        "OrderBookSnapshot",
        "TradeEvent",
        "OrderBookSnapshot",
    ]


def test_depth_replay_reconstructs_local_order_book_state() -> None:
    buffer, _ = _build_capture_buffer()
    order_book_service = OrderBookService(name="replay-order-book")

    steps = replay_depth_events(buffer.to_json_lines(), order_book_service)

    assert len(steps) == 3
    assert steps[0].snapshot is not None
    assert steps[0].sequence_issue is None
    assert steps[1].snapshot is None
    assert steps[1].sequence_issue is None
    assert steps[2].snapshot is not None
    assert steps[2].sequence_issue is None

    snapshot = order_book_service.get_snapshot("BTCUSDT")
    assert snapshot is not None
    assert snapshot.best_bid.price == 100.1
    assert snapshot.best_ask.price == 100.6
    assert snapshot.sequence == 12


def test_depth_replay_tracks_non_monotonic_sequence_order() -> None:
    buffer = DepthCaptureBuffer(max_records=8)
    base_time = datetime(2026, 4, 5, 13, 0, tzinfo=timezone.utc)
    service = OrderBookService(name="order-book")

    first = service.replace_snapshot(
        "ETHUSDT",
        bids=[(1500.0, 1.0)],
        asks=[(1500.5, 1.0)],
        sequence=20,
        exchange_timestamp=base_time,
        received_at=base_time,
    )
    buffer.capture_snapshot(
        first,
        event_id="depth-a",
        event_type="order_book.snapshot",
        source="runtime.order_book",
        captured_at=base_time + timedelta(milliseconds=5),
    )
    second = service.replace_snapshot(
        "ETHUSDT",
        bids=[(1500.1, 1.1)],
        asks=[(1500.6, 0.9)],
        sequence=22,
        exchange_timestamp=base_time + timedelta(seconds=1),
        received_at=base_time + timedelta(seconds=1),
    )
    buffer.capture_snapshot(
        second,
        event_id="depth-b",
        event_type="order_book.snapshot",
        source="runtime.order_book",
        captured_at=base_time + timedelta(seconds=1, milliseconds=5),
    )
    third = service.replace_snapshot(
        "ETHUSDT",
        bids=[(1500.2, 1.2)],
        asks=[(1500.7, 0.8)],
        sequence=21,
        exchange_timestamp=base_time + timedelta(seconds=2),
        received_at=base_time + timedelta(seconds=2),
    )
    buffer.capture_snapshot(
        third,
        event_id="depth-c",
        event_type="order_book.snapshot",
        source="runtime.order_book",
        captured_at=base_time + timedelta(seconds=2, milliseconds=5),
    )

    harness = RuntimeDepthReplayHarness.from_jsonl(buffer.to_json_lines())
    steps = tuple(harness.iter_steps(OrderBookService(name="replay-order-book")))

    assert [step.sequence_issue for step in steps] == [None, "gap", "non_monotonic"]


def test_depth_replay_writes_and_reads_jsonl(tmp_path) -> None:
    buffer, _ = _build_capture_buffer()
    output_path = buffer.write_jsonl(tmp_path / "capture.jsonl")

    harness = RuntimeDepthReplayHarness.from_jsonl(output_path)
    lines = output_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 3
    assert [json.loads(line)["event_id"] for line in lines] == [
        "depth-1",
        "trade-1",
        "depth-2",
    ]
    assert [record.capture_index for record in harness.iter_records()] == [1, 2, 3]
