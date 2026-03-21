from __future__ import annotations

import logging

import pytest

from services.base import BaseService


class ExampleService(BaseService):
    def __init__(self) -> None:
        super().__init__(name="example")
        self.events: list[str] = []

    async def on_start(self) -> None:
        self.events.append("start")

    async def on_stop(self) -> None:
        self.events.append("stop")


class FailingStartService(BaseService):
    def __init__(self) -> None:
        super().__init__(name="failing")
        self.stop_calls = 0

    async def on_start(self) -> None:
        raise RuntimeError("boom")

    async def on_stop(self) -> None:
        self.stop_calls += 1


class PassiveService(BaseService):
    def __init__(self, name: str, events: list[str]) -> None:
        super().__init__(name=name)
        self.events = events

    async def on_start(self) -> None:
        self.events.append(f"start:{self.name}")

    async def on_stop(self) -> None:
        self.events.append(f"stop:{self.name}")


def test_base_service_exposes_name_logger_and_running_state() -> None:
    service = ExampleService()

    assert service.name == "example"
    assert service.is_running is False
    assert isinstance(service.logger, logging.Logger)
    assert service.logger.name == "akiba.runtime.service.example"


def test_base_service_start_and_stop_are_idempotent() -> None:
    service = ExampleService()

    async def run() -> None:
        await service.start()
        await service.start()
        await service.stop()
        await service.stop()

    import asyncio

    asyncio.run(run())

    assert service.is_running is False
    assert service.events == ["start", "stop"]


def test_base_service_clears_running_state_when_start_fails() -> None:
    service = FailingStartService()

    async def run() -> None:
        with pytest.raises(RuntimeError, match="boom"):
            await service.start()

    import asyncio

    asyncio.run(run())

    assert service.is_running is False
    assert service.stop_calls == 0


def test_service_registry_rolls_back_started_services_on_failure() -> None:
    from runtime.service_registry import ServiceRegistry

    events: list[str] = []

    class ExplodingService(PassiveService):
        async def on_start(self) -> None:
            events.append(f"start:{self.name}")
            raise RuntimeError("boom")

    registry = ServiceRegistry(
        [
            ("alpha", PassiveService("alpha", events)),
            ("beta", ExplodingService("beta", events)),
            ("gamma", PassiveService("gamma", events)),
        ]
    )

    async def run() -> None:
        with pytest.raises(RuntimeError, match="boom"):
            await registry.start_all()

    import asyncio

    asyncio.run(run())

    assert events == ["start:alpha", "start:beta", "stop:alpha"]
