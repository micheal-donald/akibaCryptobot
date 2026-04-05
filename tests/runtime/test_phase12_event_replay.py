from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from domain.events import EventMeta, KlinePayload, RuntimeEvent
from runtime.event_capture import RuntimeEventCaptureBuffer
from runtime.event_replay import (
    RuntimeEventReplayHarness,
    SignalSuppressedPayload,
    load_runtime_event_replay,
    replay_runtime_events,
)


def _make_kline_event(*, event_id: str, minute: int) -> RuntimeEvent:
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


def _make_signal_suppressed_event() -> RuntimeEvent:
    received = datetime(2026, 4, 5, 13, 0, tzinfo=timezone.utc)
    close_time = received - timedelta(seconds=15)
    return RuntimeEvent(
        meta=EventMeta(
            event_id="evt-signal",
            event_type="signal.suppressed",
            source="runtime.signal",
            symbol="ETHUSDT",
            ts_exchange=close_time,
            ts_received=received,
        ),
        payload=SignalSuppressedPayload(
            symbol="ETHUSDT",
            timeframe="1m",
            reason="duplicate_candle_close",
            candle_close_time=close_time,
            strategy_name="ensemble",
            decision_lag_ms=12.5,
        ),
    )


def test_replay_harness_loads_records_from_jsonl(tmp_path) -> None:
    buffer = RuntimeEventCaptureBuffer(max_records=8)
    first = _make_kline_event(event_id="evt-a", minute=1)
    second = _make_signal_suppressed_event()

    buffer.capture(first, captured_at=first.meta.ts_received)
    buffer.capture(second, captured_at=second.meta.ts_received)
    capture_path = buffer.write_jsonl(tmp_path / "capture.jsonl")

    harness = load_runtime_event_replay(capture_path)
    records = tuple(harness.iter_records())

    assert [record.event_id for record in records] == ["evt-a", "evt-signal"]
    assert records[1].payload_type == "SignalSuppressedPayload"


def test_replay_harness_reconstructs_runtime_events() -> None:
    buffer = RuntimeEventCaptureBuffer(max_records=8)
    first = _make_kline_event(event_id="evt-a", minute=2)
    second = _make_signal_suppressed_event()

    buffer.capture(first, captured_at=first.meta.ts_received)
    buffer.capture(second, captured_at=second.meta.ts_received)
    events = replay_runtime_events(buffer.to_json_lines())

    assert [event.meta.event_id for event in events] == ["evt-a", "evt-signal"]
    assert isinstance(events[0].payload, KlinePayload)
    assert isinstance(events[1].payload, SignalSuppressedPayload)
    assert events[1].payload.reason == "duplicate_candle_close"


def test_replay_harness_preserves_unknown_payloads() -> None:
    line = json.dumps(
        {
            "capture_index": 1,
            "event_id": "evt-raw",
            "event_type": "runtime.shutdown",
            "source": "runtime.control",
            "symbol": None,
            "ts_exchange": None,
            "ts_received": "2026-04-05T13:10:00+00:00",
            "captured_at": "2026-04-05T13:10:00+00:00",
            "payload_type": "UnknownPayload",
            "payload": {"reason": "manual"},
        }
    )

    events = replay_runtime_events([line])
    assert events[0].payload == {"reason": "manual"}


async def _run_replay_into_queue() -> tuple[str, ...]:
    buffer = RuntimeEventCaptureBuffer(max_records=8)
    first = _make_kline_event(event_id="evt-a", minute=3)
    second = _make_signal_suppressed_event()
    buffer.capture(first, captured_at=first.meta.ts_received)
    buffer.capture(second, captured_at=second.meta.ts_received)

    harness = RuntimeEventReplayHarness.from_jsonl(buffer.to_json_lines())
    queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    await harness.replay_into(queue)
    replayed = (await queue.get(), await queue.get())
    return tuple(event.meta.event_id for event in replayed)


def test_replay_harness_can_emit_into_async_queue() -> None:
    event_ids = asyncio.run(_run_replay_into_queue())

    assert event_ids == ("evt-a", "evt-signal")
