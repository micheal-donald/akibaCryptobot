"""In-memory telemetry service for the runtime shell."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from domain.events import RuntimeEvent

from .base import BaseService


@dataclass(slots=True)
class _LatencySummary:
    """Small running summary for latency samples."""

    count: int = 0
    total_ms: float = 0.0
    min_ms: float | None = None
    max_ms: float | None = None
    last_ms: float | None = None

    def record(self, value_ms: float) -> None:
        self.count += 1
        self.total_ms += value_ms
        self.last_ms = value_ms
        self.min_ms = value_ms if self.min_ms is None else min(self.min_ms, value_ms)
        self.max_ms = value_ms if self.max_ms is None else max(self.max_ms, value_ms)

    def snapshot(self) -> dict[str, float | int | None]:
        average_ms = self.total_ms / self.count if self.count else None
        return {
            "count": self.count,
            "total_ms": self.total_ms,
            "average_ms": average_ms,
            "min_ms": self.min_ms,
            "max_ms": self.max_ms,
            "last_ms": self.last_ms,
        }


@dataclass(slots=True)
class TelemetryService(BaseService):
    """Track runtime and market-data metrics in memory."""

    event_source: asyncio.Queue[RuntimeEvent] | None = None
    _event_counts: Counter[str] = field(default_factory=Counter, init=False, repr=False)
    _metric_counts: Counter[str] = field(
        default_factory=Counter, init=False, repr=False
    )
    _queue_depths: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _event_age_ms: _LatencySummary = field(
        default_factory=_LatencySummary, init=False, repr=False
    )
    _exchange_age_ms: _LatencySummary = field(
        default_factory=_LatencySummary, init=False, repr=False
    )
    _queue_lag_ms: dict[str, _LatencySummary] = field(
        default_factory=dict, init=False, repr=False
    )
    _total_events: int = field(default=0, init=False, repr=False)
    _last_seen_at: datetime | None = field(default=None, init=False, repr=False)
    _consumer_task: asyncio.Task[None] | None = field(
        default=None, init=False, repr=False
    )

    async def on_start(self) -> None:
        if self.event_source is None:
            self.logger.debug("TelemetryService started without an event source")
            return
        if self._consumer_task is None or self._consumer_task.done():
            self._consumer_task = asyncio.create_task(self._consume_events())

    async def on_stop(self) -> None:
        task = self._consumer_task
        self._consumer_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def record_event(
        self,
        event: RuntimeEvent,
        *,
        observed_at: datetime | None = None,
    ) -> None:
        """Record a runtime event without mutating the original event."""

        observed_at = observed_at or self._now()
        self._total_events += 1
        self._event_counts[event.meta.event_type] += 1
        self._last_seen_at = event.meta.ts_received
        self._record_event_age(event, observed_at=observed_at)
        self._record_exchange_age(event, observed_at=observed_at)

    def record_metric(self, metric_name: str, *, amount: int = 1) -> None:
        self._metric_counts[metric_name] += amount

    def record_queue_depth(self, queue_name: str, depth: int) -> None:
        self._queue_depths[queue_name] = depth
        self.record_metric(f"queue_depth.{queue_name}", amount=depth)

    def record_queue_lag(
        self,
        queue_name: str,
        event: RuntimeEvent,
        *,
        observed_at: datetime | None = None,
        depth: int | None = None,
    ) -> None:
        """Record a queue drain sample for a hot-path queue."""

        if depth is not None:
            self.record_queue_depth(queue_name, depth)
        lag_ms = self._calculate_age_ms(event.meta.ts_received, observed_at=observed_at)
        if lag_ms is None:
            return
        summary = self._queue_lag_ms.setdefault(queue_name, _LatencySummary())
        summary.record(lag_ms)
        self.record_metric(f"queue_lag_sample.{queue_name}")

    def record_dropped_event(
        self, consumer: str, *, event_type: str | None = None
    ) -> None:
        self.record_metric("dropped_event")
        self.record_metric(f"dropped_event.{consumer}")
        if event_type is not None:
            self.record_metric(f"dropped_event.{consumer}.{event_type}")

    def record_reconnect(self, source: str) -> None:
        self.record_metric("reconnect")
        self.record_metric(f"reconnect.{source}")

    def record_stale_symbol(self, symbol: str) -> None:
        self.record_metric("stale_symbol")
        self.record_metric(f"stale_symbol.{symbol}")

    def record_shadow_drift(self, symbol: str) -> None:
        self.record_metric("shadow_drift")
        self.record_metric(f"shadow_drift.{symbol}")

    def record_missing_symbol(self, symbol: str, *, side: str | None = None) -> None:
        self.record_metric("missing_symbol")
        self.record_metric(f"missing_symbol.{symbol}")
        if side is not None:
            self.record_metric(f"missing_symbol.{side}")

    def get_total_events(self) -> int:
        return self._total_events

    def get_event_count(self, event_type: str) -> int:
        return self._event_counts.get(event_type, 0)

    def get_event_counts(self) -> dict[str, int]:
        return dict(self._event_counts)

    def get_metric_count(self, metric_name: str) -> int:
        return self._metric_counts.get(metric_name, 0)

    def get_metric_counts(self) -> dict[str, int]:
        return dict(self._metric_counts)

    def get_queue_depth(self, queue_name: str) -> int | None:
        return self._queue_depths.get(queue_name)

    def get_queue_depths(self) -> dict[str, int]:
        return dict(self._queue_depths)

    def get_last_seen_at(self) -> datetime | None:
        return self._last_seen_at

    def get_latency_snapshot(self) -> dict[str, object]:
        """Return a read-only snapshot of runtime latency observations."""

        return {
            "event_age_ms": self._event_age_ms.snapshot(),
            "exchange_age_ms": self._exchange_age_ms.snapshot(),
            "queue_lag_ms": {
                queue_name: summary.snapshot()
                for queue_name, summary in self._queue_lag_ms.items()
            },
        }

    def get_snapshot(self) -> dict[str, object]:
        """Return a combined telemetry snapshot suitable for operator surfaces."""

        return {
            "total_events": self._total_events,
            "event_counts": self.get_event_counts(),
            "metric_counts": self.get_metric_counts(),
            "queue_depths": self.get_queue_depths(),
            "last_seen_at": (
                self._last_seen_at.isoformat()
                if self._last_seen_at is not None
                else None
            ),
            "latency": self.get_latency_snapshot(),
        }

    async def _consume_events(self) -> None:
        assert self.event_source is not None
        while True:
            event = await self.event_source.get()
            observed_at = self._now()
            self.record_event(event, observed_at=observed_at)

    def _record_event_age(self, event: RuntimeEvent, *, observed_at: datetime) -> None:
        age_ms = self._calculate_age_ms(event.meta.ts_received, observed_at=observed_at)
        if age_ms is None:
            return
        self._event_age_ms.record(age_ms)
        self.record_metric(f"event_age_sample.{event.meta.event_type}")

    def _record_exchange_age(
        self, event: RuntimeEvent, *, observed_at: datetime
    ) -> None:
        if event.meta.ts_exchange is None:
            return
        age_ms = self._calculate_age_ms(
            event.meta.ts_exchange, observed_at=event.meta.ts_received
        )
        if age_ms is None:
            return
        self._exchange_age_ms.record(age_ms)
        self.record_metric(f"exchange_age_sample.{event.meta.event_type}")

    @staticmethod
    def _calculate_age_ms(
        start: datetime | None, *, observed_at: datetime | None
    ) -> float | None:
        if start is None or observed_at is None:
            return None
        return (observed_at - start).total_seconds() * 1000.0

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
