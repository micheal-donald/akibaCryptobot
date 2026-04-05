"""Runtime shell for the event-driven architecture."""

from __future__ import annotations

import asyncio
import logging
import inspect
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from domain.events import (
    OrderIntentPayload,
    OrderSubmittedPayload,
    ProtectionSubmittedPayload,
    RuntimeEvent,
)
from ports.exchange import (
    ExecutionGateway,
    MarketDataBootstrapGateway,
    MarketDataGateway,
    UserStreamGateway,
)
from ports.persistence import RuntimeStore
from services.execution_service import ExecutionService
from services.market_data_service import MarketDataService
from services.persistence_service import PersistenceService
from services.position_service import PositionService
from services.signal_service import SignalService
from services.risk_service import RiskService
from services.telemetry_service import TelemetryService
from strategies.contracts import RuntimeStrategy

from .config import RuntimeConfig
from .service_registry import AsyncService, ServiceRegistry


@dataclass(slots=True)
class RuntimeEventRouter:
    """Drain runtime events into warm-path services without blocking execution."""

    name: str
    event_bus: asyncio.Queue[RuntimeEvent]
    persistence_service: PersistenceService
    telemetry_service: TelemetryService | None = None
    position_service: PositionService | None = None
    is_running: bool = field(default=False, init=False)
    logger: logging.Logger = field(init=False, repr=False)
    _consumer_task: asyncio.Task[None] | None = field(
        default=None, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self.logger = logging.getLogger(f"akiba.runtime.service.{self.name}")

    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        if self._consumer_task is None or self._consumer_task.done():
            self._consumer_task = asyncio.create_task(self._consume_events())

    async def stop(self) -> None:
        task = self._consumer_task
        if task is None:
            self.is_running = False
            return
        try:
            await asyncio.wait_for(self.event_bus.join(), timeout=2.0)
        except TimeoutError:
            pass
        self._consumer_task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self.is_running = False

    async def _consume_events(self) -> None:
        while True:
            event = await self.event_bus.get()
            drained_at = datetime.now(timezone.utc)
            try:
                if self.telemetry_service is not None:
                    self.telemetry_service.record_queue_lag(
                        "runtime.event_bus",
                        event,
                        observed_at=drained_at,
                        depth=self.event_bus.qsize(),
                    )
                await self._route_event(event)
            except Exception as exc:  # pragma: no cover - defensive logging
                self.logger.warning("runtime event routing failed: %s", exc)
            finally:
                self.event_bus.task_done()

    async def _route_event(self, event: RuntimeEvent) -> None:
        if self.telemetry_service is not None:
            self.telemetry_service.record_event(event)

        if self.position_service is not None:
            await self._route_position_event(event)

        enqueued = self.persistence_service.enqueue_event(event)
        if not enqueued and self.telemetry_service is not None:
            self.telemetry_service.record_dropped_event(
                "persistence",
                event_type=event.meta.event_type,
            )
        elif self.telemetry_service is not None:
            self.telemetry_service.record_queue_depth(
                "persistence",
                self.persistence_service.get_queue_depth(),
            )

    async def _route_position_event(self, event: RuntimeEvent) -> None:
        if event.meta.event_type in {"execution.order_update", "execution.fill"}:
            await self.position_service.handle_execution_event(event)
            return

        if event.meta.event_type != "position.protective_submitted":
            return

        payload = event.payload
        if not isinstance(payload, ProtectionSubmittedPayload):
            return
        if payload.status != "native_submitted":
            return

        self.position_service.mark_protective_orders(
            payload.client_order_id,
            payload.submitted_order_ids,
            stop_loss=payload.stop_loss,
            take_profit=payload.take_profit,
            trailing_stop=payload.trailing_stop,
        )


@dataclass(slots=True)
class RuntimeApp:
    """Owns the event bus and service lifecycle for the new runtime shell."""

    config: RuntimeConfig
    services: ServiceRegistry = field(default_factory=ServiceRegistry)
    event_bus: asyncio.Queue[RuntimeEvent] = field(init=False)
    is_running: bool = field(default=False, init=False)
    runtime_store: RuntimeStore | None = field(default=None, init=False, repr=False)
    market_data_service: MarketDataService | None = field(
        default=None, init=False, repr=False
    )
    signal_service: SignalService | None = field(default=None, init=False, repr=False)
    signal_event_source: asyncio.Queue[RuntimeEvent] | None = field(
        default=None, init=False, repr=False
    )
    risk_service: RiskService | None = field(default=None, init=False, repr=False)
    execution_service: ExecutionService | None = field(
        default=None, init=False, repr=False
    )
    persistence_service: PersistenceService | None = field(
        default=None, init=False, repr=False
    )
    telemetry_service: TelemetryService | None = field(
        default=None, init=False, repr=False
    )
    position_service: PositionService | None = field(
        default=None, init=False, repr=False
    )
    event_router: RuntimeEventRouter | None = field(
        default=None, init=False, repr=False
    )
    _runtime_store_initialized: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        # Keep the runtime-owned event bus unbounded so execution never waits on
        # warm-path persistence backpressure. Persistence has its own bounded queue.
        self.event_bus = asyncio.Queue()

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

    def get_telemetry_snapshot(self) -> dict[str, object] | None:
        """Return a read-only runtime telemetry snapshot when telemetry is active."""

        telemetry_service = self.telemetry_service
        if telemetry_service is None:
            return None

        snapshot = telemetry_service.get_snapshot()
        persistence_service = self.persistence_service
        if persistence_service is not None:
            last_failure_at, last_failure_reason = (
                persistence_service.get_last_failure()
            )
            snapshot["persistence"] = {
                "queue_depth": persistence_service.get_queue_depth(),
                "high_watermark": persistence_service.get_high_watermark(),
                "persisted_counts": persistence_service.get_persisted_counts(),
                "failure_counts": persistence_service.get_failure_counts(),
                "last_failure_at": last_failure_at,
                "last_failure_reason": last_failure_reason,
            }
        snapshot["event_bus_depth"] = self.event_bus.qsize()
        snapshot["captured_at"] = datetime.now(timezone.utc).isoformat()
        return snapshot

    @classmethod
    def build_phase4(
        cls,
        config: RuntimeConfig,
        *,
        execution_gateway: ExecutionGateway,
        runtime_store: RuntimeStore,
        user_stream_gateway: UserStreamGateway | None = None,
        max_open_positions: int = 0,
        kill_switch_enabled: bool = False,
    ) -> "RuntimeApp":
        """Build the Phase 4 runtime shell with risk, execution, and persistence."""

        app = cls(config=config)
        app.runtime_store = runtime_store

        app.position_service = PositionService(
            name="position",
            event_bus=app.event_bus,
        )
        app.execution_service = ExecutionService(
            name="execution",
            execution_gateway=execution_gateway,
            user_stream_gateway=user_stream_gateway,
            event_bus=app.event_bus,
        )
        app.risk_service = RiskService(
            name="risk",
            event_bus=app.event_bus,
            execution_service=app.execution_service,
            position_source=app.position_service,
            max_open_positions=max_open_positions,
            kill_switch_enabled=kill_switch_enabled,
        )
        app.persistence_service = PersistenceService(
            name="persistence",
            runtime_store=runtime_store,
            max_queue_size=config.queue_maxsize or 256,
        )
        app.telemetry_service = TelemetryService(name="telemetry")
        app.event_router = RuntimeEventRouter(
            name="runtime-router",
            event_bus=app.event_bus,
            persistence_service=app.persistence_service,
            telemetry_service=app.telemetry_service,
            position_service=app.position_service,
        )

        for name, service in (
            ("risk", app.risk_service),
            ("persistence", app.persistence_service),
            ("telemetry", app.telemetry_service),
            ("runtime-router", app.event_router),
            ("position", app.position_service),
            ("execution", app.execution_service),
        ):
            app.register_service(name, service)

        return app

    @classmethod
    def build_phase5(
        cls,
        config: RuntimeConfig,
        *,
        market_data_gateway: MarketDataGateway,
        execution_gateway: ExecutionGateway,
        runtime_store: RuntimeStore,
        strategy: RuntimeStrategy,
        market_data_bootstrap_gateway: MarketDataBootstrapGateway | None = None,
        user_stream_gateway: UserStreamGateway | None = None,
        max_open_positions: int = 0,
        kill_switch_enabled: bool = False,
    ) -> "RuntimeApp":
        """Build the Phase 5 runtime shell with market-data and signal wiring."""

        app = cls.build_phase4(
            config,
            execution_gateway=execution_gateway,
            runtime_store=runtime_store,
            user_stream_gateway=user_stream_gateway,
            max_open_positions=max_open_positions,
            kill_switch_enabled=kill_switch_enabled,
        )
        app.signal_event_source = asyncio.Queue()
        app.market_data_service = MarketDataService(
            name="market-data",
            gateway=market_data_gateway,
            runtime_config=config,
            event_bus=app.event_bus,
            bootstrap_gateway=market_data_bootstrap_gateway,
            event_sinks=(app.signal_event_source,),
        )
        app.signal_service = SignalService(
            name="signal",
            strategy=strategy,
            event_bus=app.event_bus,
            event_source=app.signal_event_source,
            market_state_source=app.market_data_service,
            position_source=app.position_service,
            risk_service=app.risk_service,
            supported_timeframes=config.kline_intervals,
        )

        app.register_service("market-data", app.market_data_service)
        app.register_service("signal", app.signal_service)
        return app

    async def start(self) -> None:
        if self.is_running:
            return
        try:
            await self._initialize_runtime_store()
            await self.services.start_all()
        except Exception:
            await self._close_runtime_store()
            self.is_running = False
            raise
        self.is_running = True

    async def stop(self) -> None:
        if not self.is_running and len(self.services) == 0:
            return
        try:
            await self.services.stop_all()
        finally:
            await self._close_runtime_store()
            self.is_running = False

    async def submit_order_intent(
        self,
        intent: OrderIntentPayload,
    ) -> OrderSubmittedPayload | None:
        """Evaluate an order intent through runtime risk before execution."""

        if self.risk_service is None:
            raise RuntimeError("Phase 4 runtime services are not configured")
        return await self.risk_service.submit_order_intent(intent)

    async def _initialize_runtime_store(self) -> None:
        if self.runtime_store is None or self._runtime_store_initialized:
            return
        initialize = getattr(self.runtime_store, "initialize", None)
        if initialize is None or not callable(initialize):
            self._runtime_store_initialized = True
            return
        result = initialize()
        if inspect.isawaitable(result):
            await result
        self._runtime_store_initialized = True

    async def _close_runtime_store(self) -> None:
        if self.runtime_store is None or not self._runtime_store_initialized:
            return
        close = getattr(self.runtime_store, "close", None)
        self._runtime_store_initialized = False
        if close is None or not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            await result
