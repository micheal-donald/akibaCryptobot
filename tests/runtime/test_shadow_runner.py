from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from domain.events import BookTickerPayload, EventMeta, RuntimeEvent
from runtime.config import RuntimeConfig
from runtime.shadow import ShadowRuntime, build_shadow_runtime


class FakeMarketDataGateway:
    def __init__(self) -> None:
        self.callbacks: dict[str, object] = {}
        self.calls: list[object] = []
        self.connected = False

    async def connect(self) -> None:
        self.calls.append("connect")
        self.connected = True

    async def disconnect(self) -> None:
        self.calls.append("disconnect")
        self.connected = False

    def register_callback(self, event_type: str, callback: object) -> None:
        self.callbacks[event_type] = callback

    async def subscribe_book_ticker(self, symbol: str) -> None:
        self.calls.append(("book_ticker", symbol))

    async def subscribe_kline(self, symbol: str, interval: str = "1m") -> None:
        self.calls.append(("kline", symbol, interval))


class RecordingGatewayFactory(FakeMarketDataGateway):
    instances: list["RecordingGatewayFactory"] = []

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        super().__init__()
        RecordingGatewayFactory.instances.append(self)


def make_config(*, shadow_mode: bool = True) -> RuntimeConfig:
    return RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        candle_history_limit=16,
        shadow_mode=shadow_mode,
    )


def test_build_shadow_runtime_refuses_non_shadow_config() -> None:
    with pytest.raises(ValueError, match="shadow_mode=True"):
        build_shadow_runtime(make_config(shadow_mode=False), FakeMarketDataGateway())


def test_build_shadow_runtime_only_wires_non_trading_services() -> None:
    runtime = build_shadow_runtime(make_config(), FakeMarketDataGateway())

    assert isinstance(runtime, ShadowRuntime)
    assert runtime.app.config.shadow_mode is True
    assert runtime.app.services.names() == ("telemetry", "market-data")
    assert runtime.app.is_running is False


def test_build_shadow_runtime_uses_adapter_gateway_by_default(monkeypatch) -> None:
    import runtime.shadow as shadow_module

    RecordingGatewayFactory.instances.clear()
    monkeypatch.setattr(
        shadow_module, "BinanceWebSocketGateway", RecordingGatewayFactory
    )

    runtime = build_shadow_runtime(make_config())

    assert RecordingGatewayFactory.instances
    assert isinstance(runtime.market_data_service.gateway, RecordingGatewayFactory)
    assert runtime.market_data_service.gateway is RecordingGatewayFactory.instances[0]


def test_shadow_runtime_starts_and_routes_market_events_to_telemetry() -> None:
    gateway = FakeMarketDataGateway()
    runtime = build_shadow_runtime(make_config(), gateway)

    ts = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)

    async def run() -> None:
        await runtime.start()
        await gateway.callbacks["market.tick"](
            RuntimeEvent(
                meta=EventMeta(
                    event_id="evt-1",
                    event_type="market.tick",
                    source="binance.ws.book_ticker",
                    symbol="BTCUSDT",
                    ts_exchange=ts,
                    ts_received=ts,
                ),
                payload=BookTickerPayload(
                    bid_price=100.0,
                    bid_qty=1.0,
                    ask_price=100.2,
                    ask_qty=2.0,
                ),
            )
        )
        await asyncio.wait_for(_wait_for_total(runtime.telemetry_service, 1), timeout=1)
        await runtime.stop()

    asyncio.run(run())

    assert gateway.calls == [
        "connect",
        ("book_ticker", "BTCUSDT"),
        ("kline", "BTCUSDT", "1m"),
        "disconnect",
    ]
    assert runtime.telemetry_service.get_total_events() == 1
    assert runtime.market_data_service.get_symbol_state("BTC/USDT") is not None
    assert runtime.app.is_running is False


async def _wait_for_total(service, expected: int) -> None:
    while service.get_total_events() < expected:
        await asyncio.sleep(0)
