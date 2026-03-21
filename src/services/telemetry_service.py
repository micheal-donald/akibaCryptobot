"""In-memory telemetry service for the phase 1 runtime shell."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from domain.events import RuntimeEvent

from .base import BaseService


@dataclass(slots=True)
class TelemetryService(BaseService):
    """Track simple runtime metrics in memory."""

    event_source: asyncio.Queue[RuntimeEvent] | None = None
    _event_counts: Counter[str] = field(default_factory=Counter, init=False, repr=False)
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

    def get_total_events(self) -> int:
        return self._total_events

    def get_event_count(self, event_type: str) -> int:
        return self._event_counts.get(event_type, 0)

    def get_event_counts(self) -> dict[str, int]:
        return dict(self._event_counts)

    def get_last_seen_at(self) -> datetime | None:
        return self._last_seen_at

    async def _consume_events(self) -> None:
        assert self.event_source is not None
        while True:
            event = await self.event_source.get()
            self.record_event(event)
