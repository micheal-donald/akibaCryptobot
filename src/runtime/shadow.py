"""Shadow-mode runtime helpers for the phase 1 event-driven shell."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Callable

from adapters.binance.ws_gateway import BinanceWebSocketGateway
from runtime.config import RuntimeConfig
from ports.exchange import MarketDataGateway
from services.market_data_service import MarketDataService
from services.shadow_comparator import ShadowComparator
from services.telemetry_service import TelemetryService

from .app import RuntimeApp


@dataclass(slots=True)
class ShadowRuntime:
    """Container for the non-trading runtime stack."""

    app: RuntimeApp
    market_data_service: MarketDataService
    telemetry_service: TelemetryService
    shadow_comparator: ShadowComparator | None = None

    async def start(self) -> None:
        await self.app.start()

    async def stop(self) -> None:
        await self.app.stop()


def build_shadow_runtime(
    config: RuntimeConfig,
    gateway: MarketDataGateway | None = None,
    legacy_state_provider: Callable[[], Mapping[str, object]] | None = None,
    *,
    comparison_interval_seconds: float = 5.0,
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
    shadow_comparator: ShadowComparator | None = None
    if legacy_state_provider is not None:
        shadow_comparator = ShadowComparator(
            name="shadow-comparator",
            current_state_provider=market_data.get_all_symbol_states,
            legacy_state_provider=legacy_state_provider,
            telemetry_service=telemetry,
            comparison_interval_seconds=comparison_interval_seconds,
        )

    app.register_service("telemetry", telemetry)
    app.register_service("market-data", market_data)
    if shadow_comparator is not None:
        app.register_service("shadow-comparator", shadow_comparator)

    return ShadowRuntime(
        app=app,
        market_data_service=market_data,
        telemetry_service=telemetry,
        shadow_comparator=shadow_comparator,
    )
