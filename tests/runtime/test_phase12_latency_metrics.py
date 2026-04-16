from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import pytest

from domain.events import EventMeta, KlinePayload, RuntimeEvent
from runtime.app import RuntimeEventRouter
from services.telemetry_service import TelemetryService


class DummyPersistenceService:
    def __init__(self) -> None:
        self.enqueued = 0
        self.depth = 0

    def enqueue_event(self, event: RuntimeEvent) -> bool:
        del event
        self.enqueued += 1
        self.depth = self.enqueued
        return True

    def get_queue_depth(self) -> int:
        return self.depth

    def get_high_watermark(self) -> int:
        return self.depth

    def get_persisted_counts(self) -> dict[str, int]:
        return {"events": self.enqueued, "recovery_reports": 0}

    def get_failure_counts(self) -> dict[str, int]:
        return {"failed_writes": 0, "dropped_jobs": 0}

    def get_last_failure(self):
        return None, None


def make_event(*, event_id: str = "evt-1") -> RuntimeEvent:
    ts_exchange = datetime(2026, 4, 5, 10, 0, tzinfo=timezone.utc)
    ts_received = ts_exchange + timedelta(seconds=2)
    return RuntimeEvent(
        meta=EventMeta(
            event_id=event_id,
            event_type="market.kline",
            source="binance.ws.kline.1m",
            symbol="BTCUSDT",
            ts_exchange=ts_exchange,
            ts_received=ts_received,
        ),
        payload=KlinePayload(
            interval="1m",
            open_time=ts_exchange,
            close_time=ts_received,
            open=100.0,
            high=101.0,
            low=99.5,
            close=100.5,
            volume=25.0,
            is_closed=True,
        ),
    )


def test_telemetry_service_records_age_and_queue_snapshots() -> None:
    telemetry = TelemetryService(name="telemetry")
    event = make_event()
    observed_at = event.meta.ts_received + timedelta(milliseconds=250)

    telemetry.record_event(event, observed_at=observed_at)
    telemetry.record_queue_lag(
        "runtime.event_bus",
        event,
        observed_at=observed_at,
        depth=3,
    )

    snapshot = telemetry.get_snapshot()
    assert snapshot["total_events"] == 1
    assert snapshot["queue_depths"]["runtime.event_bus"] == 3
    assert snapshot["latency"]["event_age_ms"]["count"] == 1
    assert snapshot["latency"]["event_age_ms"]["last_ms"] == pytest.approx(250.0)
    assert snapshot["latency"]["exchange_age_ms"]["last_ms"] == pytest.approx(2000.0)
    assert snapshot["latency"]["queue_lag_ms"]["runtime.event_bus"]["count"] == 1
    assert snapshot["latency"]["queue_lag_ms"]["runtime.event_bus"][
        "last_ms"
    ] == pytest.approx(250.0)


def test_telemetry_service_tolerates_naive_timestamps() -> None:
    """Regression: a naive ts_exchange paired with an aware observed_at
    used to raise TypeError inside _calculate_age_ms and cascade up into
    the runtime-router warning log. The math layer now coerces to UTC."""

    telemetry = TelemetryService(name="telemetry")
    naive_exchange = datetime(2026, 4, 5, 10, 0)  # naive on purpose
    aware_received = datetime(2026, 4, 5, 10, 0, 2, tzinfo=timezone.utc)
    event = RuntimeEvent(
        meta=EventMeta(
            event_id="evt-naive",
            event_type="market.kline",
            source="binance.ws.kline.1m",
            symbol="BTCUSDT",
            ts_exchange=naive_exchange,
            ts_received=aware_received,
        ),
        payload=KlinePayload(
            interval="1m",
            open_time=aware_received,
            close_time=aware_received,
            open=100.0,
            high=101.0,
            low=99.5,
            close=100.5,
            volume=25.0,
            is_closed=True,
        ),
    )

    observed_at = aware_received + timedelta(milliseconds=250)
    telemetry.record_event(event, observed_at=observed_at)
    telemetry.record_queue_lag(
        "runtime.event_bus",
        event,
        observed_at=observed_at,
        depth=1,
    )

    snapshot = telemetry.get_snapshot()
    # Both subtractions should have produced finite samples instead of
    # raising — naive ts_exchange is interpreted as UTC.
    assert snapshot["latency"]["exchange_age_ms"]["count"] == 1
    assert snapshot["latency"]["exchange_age_ms"]["last_ms"] == pytest.approx(2000.0)
    assert snapshot["latency"]["queue_lag_ms"]["runtime.event_bus"]["count"] == 1


def test_telemetry_service_ignores_negative_exchange_age_samples() -> None:
    telemetry = TelemetryService(name="telemetry")
    event = make_event(event_id="evt-negative")
    invalid_received = event.meta.ts_exchange - timedelta(seconds=1)
    invalid_event = RuntimeEvent(
        meta=EventMeta(
            event_id=event.meta.event_id,
            event_type=event.meta.event_type,
            source=event.meta.source,
            symbol=event.meta.symbol,
            ts_exchange=event.meta.ts_exchange,
            ts_received=invalid_received,
        ),
        payload=event.payload,
    )

    telemetry.record_event(invalid_event, observed_at=invalid_received)

    snapshot = telemetry.get_snapshot()
    assert snapshot["latency"]["exchange_age_ms"]["count"] == 0
    assert telemetry.get_metric_count("exchange_age_sample.market.kline") == 0


@pytest.mark.asyncio
async def test_runtime_event_router_records_queue_lag_and_persistence_depth() -> None:
    event_bus: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    telemetry = TelemetryService(name="telemetry")
    persistence = DummyPersistenceService()
    router = RuntimeEventRouter(
        name="runtime-router",
        event_bus=event_bus,
        persistence_service=persistence,
        telemetry_service=telemetry,
    )
    event = make_event(event_id="evt-2")

    await router.start()
    await event_bus.put(event)
    await asyncio.wait_for(event_bus.join(), timeout=1.0)
    await router.stop()

    snapshot = telemetry.get_snapshot()
    assert snapshot["total_events"] == 1
    assert snapshot["queue_depths"]["runtime.event_bus"] == 0
    assert snapshot["queue_depths"]["persistence"] == 1
    assert snapshot["latency"]["queue_lag_ms"]["runtime.event_bus"]["count"] == 1
    assert persistence.enqueued == 1


class _RaisingTelemetryService(TelemetryService):
    """Telemetry stub whose queue-lag sampling always explodes."""

    def record_queue_lag(self, *args, **kwargs):  # type: ignore[override]
        raise RuntimeError("boom: offset-naive/offset-aware-style failure")


@pytest.mark.asyncio
async def test_router_still_persists_when_queue_lag_metric_raises() -> None:
    """Regression: a crashing observability sample must not drop routing.

    Before the failure-domain split, a single raise inside record_queue_lag
    aborted the try block and silently skipped _route_event — persistence
    writes were lost for every affected event."""

    event_bus: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    telemetry = _RaisingTelemetryService(name="telemetry")
    persistence = DummyPersistenceService()
    router = RuntimeEventRouter(
        name="runtime-router",
        event_bus=event_bus,
        persistence_service=persistence,
        telemetry_service=telemetry,
    )
    event = make_event(event_id="evt-isolated")

    await router.start()
    await event_bus.put(event)
    await asyncio.wait_for(event_bus.join(), timeout=1.0)
    await router.stop()

    # Routing must land even though the queue-lag sample raised.
    assert persistence.enqueued == 1
