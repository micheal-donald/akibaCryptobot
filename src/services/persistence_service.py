"""Warm-path persistence service for runtime events and recovery reports."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, cast

from domain.events import RuntimeEvent
from ports.persistence import RuntimeStore

from .base import BaseService

if TYPE_CHECKING:
    from services.recovery_service import RecoveryReport


PersistenceJobKind = Literal["event", "recovery_report"]


@dataclass(slots=True, frozen=True)
class PersistenceJob:
    """Single unit of warm-path persistence work."""

    kind: PersistenceJobKind
    payload: object
    enqueued_at: datetime


@dataclass(slots=True)
class PersistenceService(BaseService):
    """Consume runtime events asynchronously without blocking the hot path."""

    runtime_store: RuntimeStore
    max_queue_size: int = 256
    drain_timeout_seconds: float = 2.0
    _queue: asyncio.Queue[PersistenceJob] = field(init=False, repr=False)
    _consumer_task: asyncio.Task[None] | None = field(
        default=None, init=False, repr=False
    )
    _persisted_events: int = field(default=0, init=False, repr=False)
    _persisted_recovery_reports: int = field(default=0, init=False, repr=False)
    _failed_writes: int = field(default=0, init=False, repr=False)
    _dropped_jobs: int = field(default=0, init=False, repr=False)
    _high_watermark: int = field(default=0, init=False, repr=False)
    _last_failure_reason: str | None = field(default=None, init=False, repr=False)
    _last_failure_at: datetime | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        BaseService.__post_init__(self)
        queue_size = max(int(self.max_queue_size), 1)
        self._queue = asyncio.Queue(maxsize=queue_size)

    async def on_start(self) -> None:
        if self._consumer_task is None or self._consumer_task.done():
            self._consumer_task = asyncio.create_task(self._consume_jobs())

    async def on_stop(self) -> None:
        task = self._consumer_task
        if task is None:
            return

        await self.wait_until_drained(timeout_seconds=self.drain_timeout_seconds)
        self._consumer_task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def enqueue_event(self, event: RuntimeEvent) -> bool:
        """Queue a runtime event for persistence without blocking."""

        return self._enqueue_job(PersistenceJob("event", event, self._now()))

    def enqueue_recovery_report(self, report: "RecoveryReport") -> bool:
        """Queue a recovery report for persistence without blocking."""

        return self._enqueue_job(PersistenceJob("recovery_report", report, self._now()))

    async def wait_until_drained(self, timeout_seconds: float | None = None) -> bool:
        """Wait until the internal queue is empty, bounded by timeout."""

        try:
            if timeout_seconds is None:
                await self._queue.join()
            else:
                await asyncio.wait_for(self._queue.join(), timeout_seconds)
            return True
        except asyncio.TimeoutError:
            return False

    def get_queue_depth(self) -> int:
        """Return the current persistence backlog depth."""

        return self._queue.qsize()

    def get_high_watermark(self) -> int:
        """Return the highest observed backlog depth."""

        return self._high_watermark

    def get_persisted_counts(self) -> dict[str, int]:
        """Return a snapshot of persisted item counters."""

        return {
            "events": self._persisted_events,
            "recovery_reports": self._persisted_recovery_reports,
        }

    def get_failure_counts(self) -> dict[str, int]:
        """Return a snapshot of failure and drop counters."""

        return {
            "failed_writes": self._failed_writes,
            "dropped_jobs": self._dropped_jobs,
        }

    def get_last_failure(self) -> tuple[datetime | None, str | None]:
        """Return the timestamp and reason of the latest persistence failure."""

        return self._last_failure_at, self._last_failure_reason

    def _enqueue_job(self, job: PersistenceJob) -> bool:
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            self._dropped_jobs += 1
            return False
        self._high_watermark = max(self._high_watermark, self._queue.qsize())
        return True

    async def _consume_jobs(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._persist_job(job)
            except Exception as exc:
                self._failed_writes += 1
                self._last_failure_at = self._now()
                self._last_failure_reason = f"{job.kind}: {exc}"
                self.logger.warning("Persistence job failed: %s", exc)
            finally:
                self._queue.task_done()

    async def _persist_job(self, job: PersistenceJob) -> None:
        if job.kind == "event":
            await self.runtime_store.record_event(cast(RuntimeEvent, job.payload))
            self._persisted_events += 1
            return

        await self.runtime_store.record_recovery_report(job.payload)
        self._persisted_recovery_reports += 1

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
