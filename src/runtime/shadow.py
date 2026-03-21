"""Shadow-mode runtime helpers for the phase 1 event-driven shell."""

from __future__ import annotations

from dataclasses import dataclass

from adapters.binance.ws_gateway import BinanceWebSocketGateway
from runtime.config import RuntimeConfig
from ports.exchange import MarketDataGateway
from services.market_data_service import MarketDataService
from services.telemetry_service import TelemetryService

from .app import RuntimeApp


@dataclass(slots=True)
class ShadowRuntime:
    """Container for the non-trading runtime stack."""

    app: RuntimeApp
    market_data_service: MarketDataService
    telemetry_service: TelemetryService

    async def start(self) -> None:
        await self.app.start()

    async def stop(self) -> None:
        await self.app.stop()


def build_shadow_runtime(
    config: RuntimeConfig,
    gateway: MarketDataGateway | None = None,
) -> ShadowRuntime:
    """Construct a shadow-mode runtime with no execution services."""

    if not config.shadow_mode:
        raise ValueError("shadow runtime requires shadow_mode=True")

    app = RuntimeApp.build(config)

    if gateway is None:
        gateway = BinanceWebSocketGateway(api_key="", api_secret="", testnet=True)

    telemetry = TelemetryService(name="telemetry", event_source=app.event_bus)
    market_data = MarketDataService(
        name="market-data",
        gateway=gateway,
        runtime_config=config,
        event_bus=app.event_bus,
    )

    app.register_service("telemetry", telemetry)
    app.register_service("market-data", market_data)

    return ShadowRuntime(
        app=app,
        market_data_service=market_data,
        telemetry_service=telemetry,
    )
