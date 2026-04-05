from __future__ import annotations

import json
from datetime import datetime, timezone

from runtime.depth_capture import DepthCaptureBuffer
from services.order_book_service import OrderBookService


def _build_snapshot():
    service = OrderBookService(name="order-book")
    ts = datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)
    return service.replace_snapshot(
        "BTCUSDT",
        bids=[(100.0, 1.5), (99.5, 2.0)],
        asks=[(100.5, 1.0), (101.0, 2.0)],
        sequence=21,
        exchange_timestamp=ts,
        received_at=ts,
    )


def test_depth_capture_serializes_order_book_snapshot() -> None:
    buffer = DepthCaptureBuffer(max_records=4)
    snapshot = _build_snapshot()
    captured_at = datetime(2026, 4, 5, 12, 0, 1, tzinfo=timezone.utc)

    record = buffer.capture_snapshot(
        snapshot,
        event_id="depth-1",
        event_type="order_book.snapshot",
        source="runtime.order_book",
        captured_at=captured_at,
    )

    assert record.capture_index == 1
    assert record.symbol == "BTCUSDT"
    assert record.sequence == 21
    assert record.payload_type == "OrderBookSnapshot"
    assert record.payload["best_bid"] == {"price": 100.0, "quantity": 1.5}
    assert record.payload["best_ask"] == {"price": 100.5, "quantity": 1.0}
    assert record.payload["bids"][0] == {"price": 100.0, "quantity": 1.5}
    assert record.payload["asks"][0] == {"price": 100.5, "quantity": 1.0}


def test_depth_capture_serializes_trade_events_and_preserves_order() -> None:
    buffer = DepthCaptureBuffer(max_records=4)
    ts = datetime(2026, 4, 5, 12, 1, tzinfo=timezone.utc)

    first = buffer.capture_trade(
        event_id="trade-1",
        event_type="market.trade",
        source="binance.ws.trade",
        symbol="ETHUSDT",
        side="buy",
        price=1500.0,
        quantity=0.25,
        sequence=100,
        ts_exchange=ts,
        ts_received=ts,
        captured_at=ts,
        extra={"trade_id": "abc"},
    )
    second = buffer.capture_trade(
        event_id="trade-2",
        event_type="market.trade",
        source="binance.ws.trade",
        symbol="ETHUSDT",
        side="sell",
        price=1501.0,
        quantity=0.1,
        sequence=101,
        ts_exchange=ts,
        ts_received=ts,
        captured_at=ts,
    )

    assert [record.capture_index for record in buffer.get_records()] == [1, 2]
    assert first.payload["trade_id"] == "abc"
    assert second.payload["side"] == "sell"
    assert second.sequence == 101


def test_depth_capture_writes_jsonl(tmp_path) -> None:
    buffer = DepthCaptureBuffer(max_records=2)
    snapshot = _build_snapshot()
    ts = datetime(2026, 4, 5, 12, 0, 1, tzinfo=timezone.utc)
    buffer.capture_snapshot(
        snapshot,
        event_id="depth-1",
        event_type="order_book.snapshot",
        source="runtime.order_book",
        captured_at=ts,
    )
    buffer.capture_trade(
        event_id="trade-1",
        event_type="market.trade",
        source="binance.ws.trade",
        symbol="BTCUSDT",
        side="buy",
        price=100.2,
        quantity=0.5,
        sequence=22,
        ts_exchange=ts,
        ts_received=ts,
        captured_at=ts,
    )

    output_path = buffer.write_jsonl(tmp_path / "depth.jsonl")
    lines = output_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert [json.loads(line)["event_id"] for line in lines] == [
        "depth-1",
        "trade-1",
    ]


def test_depth_capture_is_bounded() -> None:
    buffer = DepthCaptureBuffer(max_records=1)
    snapshot = _build_snapshot()
    ts = datetime(2026, 4, 5, 12, 0, 1, tzinfo=timezone.utc)
    buffer.capture_snapshot(
        snapshot,
        event_id="depth-1",
        event_type="order_book.snapshot",
        source="runtime.order_book",
        captured_at=ts,
    )
    buffer.capture_trade(
        event_id="trade-1",
        event_type="market.trade",
        source="binance.ws.trade",
        symbol="BTCUSDT",
        side="buy",
        price=100.2,
        quantity=0.5,
        sequence=22,
        ts_exchange=ts,
        ts_received=ts,
        captured_at=ts,
    )

    records = buffer.get_records()
    assert len(records) == 1
    assert records[0].event_id == "trade-1"
