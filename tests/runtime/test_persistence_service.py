from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from domain.events import EventMeta, RuntimeEvent
from services.persistence_service import PersistenceService
from services.recovery_service import RecoveryIssue, RecoveryReport


@dataclass
class FakeRuntimeStore:
    events: list[RuntimeEvent]
    reports: list[RecoveryReport]
    fail_events: bool = False
    fail_reports: bool = False
    fail_begin_batch: bool = False
    begin_batch_calls: int = 0
    commit_batch_calls: int = 0
    rollback_batch_calls: int = 0

    async def record_event(self, event: RuntimeEvent) -> None:
        if self.fail_events:
            raise RuntimeError("event write failed")
        self.events.append(event)

    async def record_recovery_report(self, report: RecoveryReport) -> None:
        if self.fail_reports:
            raise RuntimeError("report write failed")
        self.reports.append(report)

    async def begin_batch(self) -> None:
        if self.fail_begin_batch:
            raise RuntimeError("begin batch failed")
        self.begin_batch_calls += 1

    async def commit_batch(self) -> None:
        self.commit_batch_calls += 1

    async def rollback_batch(self) -> None:
        self.rollback_batch_calls += 1


def build_event(event_type: str = "execution.fill") -> RuntimeEvent:
    return RuntimeEvent(
        meta=EventMeta(
            event_id="evt-1",
            event_type=event_type,
            source="test",
            symbol="BTC/USDT",
            ts_exchange=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
            ts_received=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
        ),
        payload={"type": event_type},
    )


def build_recovery_report() -> RecoveryReport:
    return RecoveryReport(
        processed_trades=1,
        restored_symbols=("BTC/USDT",),
        protected_symbols=(),
        orphaned_symbols=(),
        unprotected_symbols=("BTC/USDT",),
        reconcile_error_symbols=(),
        skipped_symbols=(),
        issues=(
            RecoveryIssue(
                symbol="BTC/USDT",
                kind="unprotected_position",
                reason="native protection missing after restart",
            ),
        ),
    )


def test_persistence_service_enqueues_and_persists_events() -> None:
    store = FakeRuntimeStore(events=[], reports=[])
    service = PersistenceService(
        name="persistence",
        runtime_store=store,
        max_queue_size=8,
    )

    async def run() -> None:
        await service.start()
        assert service.enqueue_event(build_event())
        assert service.enqueue_recovery_report(build_recovery_report())
        assert await service.wait_until_drained(timeout_seconds=1.0)
        await service.stop()

    asyncio.run(run())

    assert store.events and store.reports
    assert service.get_persisted_counts() == {
        "events": 1,
        "recovery_reports": 1,
    }
    assert service.get_failure_counts() == {
        "failed_writes": 0,
        "dropped_jobs": 0,
    }
    assert service.get_queue_depth() == 0


def test_persistence_service_batches_jobs_when_store_supports_transactions() -> None:
    store = FakeRuntimeStore(events=[], reports=[])
    service = PersistenceService(
        name="persistence",
        runtime_store=store,
        max_queue_size=8,
        max_batch_size=8,
        flush_interval_seconds=0.2,
    )

    assert service.enqueue_event(build_event("execution.fill"))
    assert service.enqueue_event(build_event("execution.order_update"))
    assert service.enqueue_recovery_report(build_recovery_report())

    async def run() -> None:
        await service.start()
        assert await service.wait_until_drained(timeout_seconds=1.0)
        await service.stop()

    asyncio.run(run())

    assert len(store.events) == 2
    assert len(store.reports) == 1
    assert store.begin_batch_calls == 1
    assert store.commit_batch_calls == 1
    assert store.rollback_batch_calls == 0


def test_persistence_service_falls_back_when_batch_begin_fails() -> None:
    store = FakeRuntimeStore(events=[], reports=[], fail_begin_batch=True)
    service = PersistenceService(
        name="persistence",
        runtime_store=store,
        max_queue_size=8,
        max_batch_size=8,
        flush_interval_seconds=0.2,
    )

    assert service.enqueue_event(build_event("execution.fill"))
    assert service.enqueue_recovery_report(build_recovery_report())

    async def run() -> None:
        await service.start()
        assert await service.wait_until_drained(timeout_seconds=1.0)
        await service.stop()

    asyncio.run(run())

    # begin_batch failed, but jobs are still persisted via individual fallback.
    assert len(store.events) == 1
    assert len(store.reports) == 1
    assert store.begin_batch_calls == 0
    assert store.commit_batch_calls == 0


def test_persistence_service_drops_jobs_when_queue_is_full() -> None:
    store = FakeRuntimeStore(events=[], reports=[])
    service = PersistenceService(
        name="persistence",
        runtime_store=store,
        max_queue_size=1,
    )

    assert service.enqueue_event(build_event())
    assert not service.enqueue_recovery_report(build_recovery_report())
    assert service.get_queue_depth() == 1
    assert service.get_failure_counts()["dropped_jobs"] == 1


def test_persistence_service_records_store_failures_without_bubbling() -> None:
    store = FakeRuntimeStore(events=[], reports=[], fail_events=True)
    service = PersistenceService(
        name="persistence",
        runtime_store=store,
        max_queue_size=8,
    )

    async def run() -> None:
        await service.start()
        assert service.enqueue_event(build_event())
        assert await service.wait_until_drained(timeout_seconds=1.0)
        await service.stop()

    asyncio.run(run())

    failure_time, failure_reason = service.get_last_failure()
    assert failure_time is not None
    assert failure_reason is not None
    assert "event write failed" in failure_reason
    assert service.get_failure_counts()["failed_writes"] == 1
    assert store.events == []
