"""Minimal runtime shell for the phase 1 event-driven architecture."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Iterable

from domain.events import RuntimeEvent

from .config import RuntimeConfig
from .service_registry import AsyncService, ServiceRegistry


@dataclass(slots=True)
class RuntimeApp:
    """Owns the event bus and service lifecycle for the new runtime shell."""

    config: RuntimeConfig
    services: ServiceRegistry = field(default_factory=ServiceRegistry)
    event_bus: asyncio.Queue[RuntimeEvent] = field(init=False)
    is_running: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.event_bus = asyncio.Queue(maxsize=self.config.event_bus_maxsize)

    @classmethod
    def build(
        cls,
        config: RuntimeConfig,
        services: Iterable[tuple[str, AsyncService]] | None = None,
    ) -> "RuntimeApp":
        """Convenience constructor for tests and future boot code."""

        registry = ServiceRegistry(services)
        return cls(config=config, services=registry)

    def register_service(self, name: str, service: AsyncService) -> None:
        self.services.register(name, service)

    async def start(self) -> None:
        if self.is_running:
            return
        await self.services.start_all()
        self.is_running = True

    async def stop(self) -> None:
        if not self.is_running and len(self.services) == 0:
            return
        await self.services.stop_all()
        self.is_running = False
