"""Service registration helpers for the phase 1 runtime shell."""

from __future__ import annotations

from collections.abc import Iterable
from services.base import AsyncService


class ServiceRegistry:
    """Ordered registry for deterministic service start and stop sequencing."""

    def __init__(
        self, services: Iterable[tuple[str, AsyncService]] | None = None
    ) -> None:
        self._services: list[tuple[str, AsyncService]] = []
        if services is not None:
            for name, service in services:
                self.register(name, service)

    def register(self, name: str, service: AsyncService) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("service name must not be empty")
        if any(existing_name == name for existing_name, _ in self._services):
            raise ValueError(f"service '{name}' is already registered")
        self._services.append((name, service))

    def has(self, name: str) -> bool:
        return any(existing_name == name for existing_name, _ in self._services)

    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self._services)

    def values(self) -> tuple[AsyncService, ...]:
        return tuple(service for _, service in self._services)

    def items(self) -> tuple[tuple[str, AsyncService], ...]:
        return tuple(self._services)

    def get(self, name: str) -> AsyncService:
        for existing_name, service in self._services:
            if existing_name == name:
                return service
        raise KeyError(name)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.has(name)

    async def start_all(self) -> None:
        started: list[AsyncService] = []
        try:
            for _, service in self._services:
                await service.start()
                started.append(service)
        except Exception:
            for service in reversed(started):
                try:
                    await service.stop()
                except Exception:
                    continue
            raise

    async def stop_all(self) -> None:
        for _, service in reversed(self._services):
            await service.stop()

    def __len__(self) -> int:
        return len(self._services)

    def __iter__(self) -> Iterable[tuple[str, AsyncService]]:
        return iter(self._services)
