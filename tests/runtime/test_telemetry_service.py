from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from domain.events import EventMeta, EventType, KlinePayload, RuntimeEvent
from services.telemetry_service import TelemetryService


def make_event(
    event_id: str,
    event_type: EventType,
    *,
    symbol: str = "BTCUSDT",
) -> RuntimeEvent:
    ts = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)
    return RuntimeEvent(
        meta=EventMeta(
            event_id=event_id,
            event_type=event_type,  # type: ignore[arg-type]
            source="binance.ws.kline.1m",
            symbol=symbol,
            ts_exchange=ts,
            ts_received=ts,
        ),
        payload=KlinePayload(
            interval="1m",
            open_time=ts,
            close_time=ts,
            open=100.0,
            high=101.0,
            low=99.5,
            close=100.5,
            volume=25.0,
            is_closed=event_type == "market.kline_closed",
        ),
    )


def test_telemetry_service_records_events_and_exposes_snapshots() -> None:
    service = TelemetryService(name="telemetry")
    event = make_event("evt-1", "market.kline")

    service.record_event(event)
    service.record_event(make_event("evt-2", "market.kline_closed"))

    assert service.get_total_events() == 2
    assert service.get_event_count("market.kline") == 1
    assert service.get_event_count("market.kline_closed") == 1
    assert service.get_event_counts() == {
        "market.kline": 1,
        "market.kline_closed": 1,
    }
    assert service.get_last_seen_at() == event.meta.ts_received

    snapshot = service.get_event_counts()
    snapshot["market.kline"] = 99
    assert service.get_event_count("market.kline") == 1


def test_telemetry_service_consumes_injected_queue_source() -> None:
    queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    service = TelemetryService(name="telemetry", event_source=queue)
    event = make_event("evt-3", "market.kline_closed")

    async def run() -> None:
        await service.start()
        await queue.put(event)
        await asyncio.wait_for(_wait_for_total(service, 1), timeout=1.0)
        await service.stop()

    asyncio.run(run())

    assert service.get_total_events() == 1
    assert service.get_event_count("market.kline_closed") == 1
    assert queue.qsize() == 0
    assert event.meta.event_id == "evt-3"
    assert event.payload.is_closed is True


def test_telemetry_service_start_and_stop_are_idempotent() -> None:
    queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    service = TelemetryService(name="telemetry", event_source=queue)

    async def run() -> None:
        await service.start()
        await service.start()
        await service.stop()
        await service.stop()

    asyncio.run(run())

    assert service.is_running is False


def test_telemetry_service_records_runtime_metrics() -> None:
    service = TelemetryService(name="telemetry")

    service.record_queue_depth("market-data", 3)
    service.record_dropped_event("shadow", event_type="market.kline")
    service.record_reconnect("market-data")
    service.record_stale_symbol("BTC/USDT")
    service.record_shadow_drift("BTC/USDT")
    service.record_missing_symbol("ETH/USDT", side="legacy")

    assert service.get_metric_count("queue_depth.market-data") == 3
    assert service.get_metric_count("dropped_event") == 1
    assert service.get_metric_count("dropped_event.shadow.market.kline") == 1
    assert service.get_metric_count("reconnect.market-data") == 1
    assert service.get_metric_count("stale_symbol.BTC/USDT") == 1
    assert service.get_metric_count("shadow_drift.BTC/USDT") == 1
    assert service.get_metric_count("missing_symbol.ETH/USDT") == 1
    assert service.get_metric_count("missing_symbol.legacy") == 1
    assert service.get_queue_depth("market-data") == 3


async def _wait_for_total(service: TelemetryService, expected: int) -> None:
    while service.get_total_events() < expected:
        await asyncio.sleep(0)
