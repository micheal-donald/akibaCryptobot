"""Persistence port contracts used by the runtime."""

from __future__ import annotations

from typing import Protocol, TYPE_CHECKING, runtime_checkable

from domain.events import RuntimeEvent

if TYPE_CHECKING:
    from services.recovery_service import RecoveryReport


@runtime_checkable
class RuntimeStore(Protocol):
    """Warm-path persistence contract for runtime events and recovery reports."""

    async def record_event(self, event: RuntimeEvent) -> None: ...

    async def record_recovery_report(self, report: RecoveryReport) -> None: ...
