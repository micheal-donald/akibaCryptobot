from __future__ import annotations

from dataclasses import dataclass

from domain.events import RuntimeEvent
from ports.persistence import RuntimeStore


@dataclass
class FakeRuntimeStore:
    events: list[RuntimeEvent]
    reports: list[object]

    async def record_event(self, event: RuntimeEvent) -> None:
        self.events.append(event)

    async def record_recovery_report(self, report: object) -> None:
        self.reports.append(report)


def test_runtime_store_protocol_accepts_runtime_store_like_implementations() -> None:
    store = FakeRuntimeStore(events=[], reports=[])

    assert isinstance(store, RuntimeStore)
