from __future__ import annotations

import asyncio

import pytest

from runtime.app import RuntimeApp
from runtime.config import RuntimeConfig
from runtime.mode import RuntimeMode
from runtime.service_registry import ServiceRegistry


class RecordingService:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    async def start(self) -> None:
        self.calls.append(f"start:{self.name}")

    async def stop(self) -> None:
        self.calls.append(f"stop:{self.name}")


def test_runtime_config_can_be_built_from_mapping() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "trading": {"pairs": ["BTC/USDT", "ETH/USDT"]},
            "runtime": {
                "kline_intervals": ["1m", "15m"],
                "candle_history_limit": 1024,
                "event_bus_maxsize": 16,
                "housekeeping_interval_seconds": 30,
                "shadow_mode": False,
            },
        }
    )

    assert config.symbols == ("BTC/USDT", "ETH/USDT")
    assert config.kline_intervals == ("1m", "15m")
    assert config.candle_history_limit == 1024
    assert config.event_bus_maxsize == 16
    assert config.housekeeping_interval_seconds == 30.0
    assert config.shadow_mode is False
    assert config.runtime_mode is RuntimeMode.RUNTIME_ENTRIES


def test_runtime_app_builds_event_bus_and_registry() -> None:
    config = RuntimeConfig(symbols=("BTC/USDT",), kline_intervals=("1m",))
    app = RuntimeApp.build(config)

    assert isinstance(app.event_bus, asyncio.Queue)
    assert app.event_bus.maxsize == 0
    assert app.services.names() == ()
    assert app.is_running is False


def test_runtime_app_starts_and_stops_services_in_deterministic_order() -> None:
    config = RuntimeConfig(symbols=("BTC/USDT",), kline_intervals=("1m",))
    calls: list[str] = []
    app = RuntimeApp.build(
        config,
        services=[
            ("alpha", RecordingService("alpha", calls)),
            ("beta", RecordingService("beta", calls)),
        ],
    )

    async def run() -> None:
        await app.start()
        assert app.is_running is True
        await app.stop()
        assert app.is_running is False

    asyncio.run(run())

    assert calls == ["start:alpha", "start:beta", "stop:beta", "stop:alpha"]


def test_service_registry_rejects_duplicate_names() -> None:
    registry = ServiceRegistry()
    calls: list[str] = []
    registry.register("alpha", RecordingService("alpha", calls))

    with pytest.raises(ValueError, match="already registered"):
        registry.register("alpha", RecordingService("alpha-2", calls))
