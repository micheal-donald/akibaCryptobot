from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from domain.events import EventMeta, KlinePayload, RuntimeEvent
from runtime.app import RuntimeEventRouter
from runtime.event_capture import CapturedRuntimeEvent, RuntimeEventCaptureBuffer


class DummyPersistenceService:
    def enqueue_event(self, event: RuntimeEvent) -> bool:
        del event
        return True

    def get_queue_depth(self) -> int:
        return 0

    def get_high_watermark(self) -> int:
        return 0

    def get_persisted_counts(self) -> dict[str, int]:
        return {"events": 0, "recovery_reports": 0}

    def get_failure_counts(self) -> dict[str, int]:
        return {"failed_writes": 0, "dropped_jobs": 0}

    def get_last_failure(self):
        return None, None


def make_kline_event(*, event_id: str, minute: int) -> RuntimeEvent:
    open_time = datetime(2026, 4, 5, 12, minute, tzinfo=timezone.utc)
    close_time = open_time + timedelta(minutes=1)
    received = close_time + timedelta(milliseconds=125)
    return RuntimeEvent(
        meta=EventMeta(
            event_id=event_id,
            event_type="market.kline_closed",
            source="binance.ws.kline.1m",
            symbol="BTCUSDT",
            ts_exchange=close_time,
            ts_received=received,
        ),
        payload=KlinePayload(
            interval="1m",
            open_time=open_time,
            close_time=close_time,
            open=100.0 + minute,
            high=101.0 + minute,
            low=99.0 + minute,
            close=100.5 + minute,
            volume=50.0 + minute,
            is_closed=True,
        ),
    )


def test_captured_runtime_event_serializes_payload_and_timestamps() -> None:
    event = make_kline_event(event_id="evt-1", minute=0)
    captured_at = event.meta.ts_received + timedelta(milliseconds=10)

    record = CapturedRuntimeEvent.from_event(
        event,
        capture_index=7,
        captured_at=captured_at,
    )

    assert record.capture_index == 7
    assert record.event_id == "evt-1"
    assert record.event_type == "market.kline_closed"
    assert record.ts_exchange == event.meta.ts_exchange.isoformat()
    assert record.ts_received == event.meta.ts_received.isoformat()
    assert record.captured_at == captured_at.isoformat()
    assert record.payload_type == "KlinePayload"
    assert record.payload == {
        "interval": "1m",
        "open_time": event.payload.open_time.isoformat(),
        "close_time": event.payload.close_time.isoformat(),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 50.0,
        "is_closed": True,
    }


def test_runtime_event_capture_buffer_is_bounded_and_ordered() -> None:
    buffer = RuntimeEventCaptureBuffer(max_records=2)
    event_one = make_kline_event(event_id="evt-1", minute=0)
    event_two = make_kline_event(event_id="evt-2", minute=1)
    event_three = make_kline_event(event_id="evt-3", minute=2)

    buffer.capture(event_one, captured_at=event_one.meta.ts_received)
    buffer.capture(event_two, captured_at=event_two.meta.ts_received)
    buffer.capture(event_three, captured_at=event_three.meta.ts_received)

    records = buffer.get_records()
    assert [record.capture_index for record in records] == [2, 3]
    assert [record.event_id for record in records] == ["evt-2", "evt-3"]


def test_runtime_event_capture_buffer_exposes_serialized_records() -> None:
    buffer = RuntimeEventCaptureBuffer(max_records=4)
    event = make_kline_event(event_id="evt-4", minute=3)

    buffer.capture(event, captured_at=event.meta.ts_received)

    serialized = buffer.get_serialized_records()
    assert serialized == (
        {
            "capture_index": 1,
            "event_id": "evt-4",
            "event_type": "market.kline_closed",
            "source": "binance.ws.kline.1m",
            "symbol": "BTCUSDT",
            "ts_exchange": event.meta.ts_exchange.isoformat(),
            "ts_received": event.meta.ts_received.isoformat(),
            "captured_at": event.meta.ts_received.isoformat(),
            "payload_type": "KlinePayload",
            "payload": {
                "interval": "1m",
                "open_time": event.payload.open_time.isoformat(),
                "close_time": event.payload.close_time.isoformat(),
                "open": 103.0,
                "high": 104.0,
                "low": 102.0,
                "close": 103.5,
                "volume": 53.0,
                "is_closed": True,
            },
        },
    )


def test_runtime_event_capture_buffer_writes_jsonl(tmp_path) -> None:
    buffer = RuntimeEventCaptureBuffer(max_records=4)
    first = make_kline_event(event_id="evt-jsonl-a", minute=6)
    second = make_kline_event(event_id="evt-jsonl-b", minute=7)

    buffer.capture(first, captured_at=first.meta.ts_received)
    buffer.capture(second, captured_at=second.meta.ts_received)

    output_path = buffer.write_jsonl(tmp_path / "capture.jsonl")
    lines = output_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert [json.loads(line)["event_id"] for line in lines] == [
        "evt-jsonl-a",
        "evt-jsonl-b",
    ]


async def _run_router_capture() -> tuple[dict[str, object], ...]:
    event_bus: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    capture = RuntimeEventCaptureBuffer(max_records=8)
    router = RuntimeEventRouter(
        name="runtime-router",
        event_bus=event_bus,
        persistence_service=DummyPersistenceService(),
        event_capture=capture,
    )
    first = make_kline_event(event_id="evt-a", minute=4)
    second = make_kline_event(event_id="evt-b", minute=5)

    await router.start()
    await event_bus.put(first)
    await event_bus.put(second)
    await asyncio.wait_for(event_bus.join(), timeout=1.0)
    await router.stop()
    return capture.get_serialized_records()


def test_runtime_event_router_captures_real_event_flow_in_order() -> None:
    records = asyncio.run(_run_router_capture())

    assert [record["capture_index"] for record in records] == [1, 2]
    assert [record["event_id"] for record in records] == ["evt-a", "evt-b"]
    assert [record["payload_type"] for record in records] == [
        "KlinePayload",
        "KlinePayload",
    ]
