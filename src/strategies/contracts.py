"""Runtime-facing strategy contracts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from domain.events import OrderIntentPayload
from domain.strategy import StrategySnapshot


@runtime_checkable
class RuntimeStrategy(Protocol):
    """Async strategy contract used by the runtime signal lane."""

    async def generate_intent(
        self,
        snapshot: StrategySnapshot,
    ) -> OrderIntentPayload | None: ...


__all__ = ["RuntimeStrategy"]
