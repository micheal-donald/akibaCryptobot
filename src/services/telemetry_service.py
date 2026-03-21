"""In-memory telemetry service for the runtime shell."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from domain.events import RuntimeEvent

from .base import BaseService


@dataclass(slots=True)
class TelemetryService(BaseService):
    """Track runtime and market-data metrics in memory."""

    event_source: asyncio.Queue[RuntimeEvent] | None = None
    _event_counts: Counter[str] = field(default_factory=Counter, init=False, repr=False)
    _metric_counts: Counter[str] = field(
        default_factory=Counter, init=False, repr=False
    )
    _queue_depths: dict[str, int] = field(default_factory=dict, init=False, repr=False)
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

    def record_event(self, event: RuntimeEvent) -> None:
        """Record a runtime event without mutating the original event."""

        self._total_events += 1
        self._event_counts[event.meta.event_type] += 1
        self._last_seen_at = event.meta.ts_received

    def record_metric(self, metric_name: str, *, amount: int = 1) -> None:
        self._metric_counts[metric_name] += amount

    def record_queue_depth(self, queue_name: str, depth: int) -> None:
        self._queue_depths[queue_name] = depth
        self.record_metric(f"queue_depth.{queue_name}", amount=depth)

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

    async def _consume_events(self) -> None:
        assert self.event_source is not None
        while True:
            event = await self.event_source.get()
            self.record_event(event)
