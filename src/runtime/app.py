"""Runtime shell for the event-driven architecture."""

from __future__ import annotations

import asyncio
import logging
import inspect
from dataclasses import asdict, dataclass, field
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
from ports.native_hot_path import NativeHotPathPorts
from ports.persistence import RuntimeStore
from services.execution_service import ExecutionService
from services.market_data_service import MarketDataService
from services.persistence_service import PersistenceService
from services.position_service import PositionService
from services.recovery_service import RecoveryReport, RestartRecoveryService
from services.signal_service import SignalService
from services.risk_service import RiskService
from services.telemetry_service import TelemetryService
from strategies.contracts import RuntimeStrategy

from .config import RuntimeConfig
from .event_capture import RuntimeEventCaptureBuffer
from .native_shadow import NativeShadowBridge, NativeShadowSnapshot
from .projection import build_runtime_projection
from .service_registry import AsyncService, ServiceRegistry


# Event types whose volume makes per-event SQLite persistence untenable and
# whose payload has no durable consumer. ``market.tick`` is the dominant
# offender (>99% of routed events on a typical run); the streaming partial
# ``market.kline`` updates collapse into the durable ``market.kline_closed``
# row when the candle finalizes, so persisting the in-progress versions adds
# write amplification without forensic value. These events still flow through
# telemetry, the in-memory capture buffer (replay), and position routing —
# only the warm-path audit-log enqueue is skipped.
EPHEMERAL_EVENT_TYPES: frozenset[str] = frozenset({"market.tick", "market.kline"})


@dataclass(slots=True)
class RuntimeEventRouter:
    """Drain runtime events into warm-path services without blocking execution."""

    name: str
    event_bus: asyncio.Queue[RuntimeEvent]
    persistence_service: PersistenceService
    telemetry_service: TelemetryService | None = None
    position_service: PositionService | None = None
    event_capture: RuntimeEventCaptureBuffer | None = None
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
            # Observability failures must not skip routing: a single broken
            # metric used to swallow the entire try block, silently dropping
            # persistence and position updates for that event. Each side
            # effect now gets its own try so a crashing capture or telemetry
            # sample only costs us that sample.
            if self.event_capture is not None:
                try:
                    self.event_capture.capture(event, captured_at=drained_at)
                except Exception as exc:
                    self.logger.warning("event capture failed: %s", exc)
            if self.telemetry_service is not None:
                try:
                    self.telemetry_service.record_queue_lag(
                        "runtime.event_bus",
                        event,
                        observed_at=drained_at,
                        depth=self.event_bus.qsize(),
                    )
                except Exception as exc:
                    self.logger.warning("queue-lag metric failed: %s", exc)
            try:
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

        if event.meta.event_type in EPHEMERAL_EVENT_TYPES:
            # Ticks and streaming partial klines are deliberately not enqueued
            # for warm-path persistence. See ``EPHEMERAL_EVENT_TYPES``: their
            # tick-volume DoSed the SQLite writer (one INSERT+COMMIT per row)
            # and latched the runtime kill switch via the cumulative drop
            # alert. Telemetry and capture above still see them.
            return

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
    recovery_service: RestartRecoveryService | None = field(
        default=None, init=False, repr=False
    )
    recovery_report: RecoveryReport | None = field(default=None, init=False, repr=False)
    event_capture: RuntimeEventCaptureBuffer | None = field(default=None, repr=False)
    native_hot_path_ports: NativeHotPathPorts | None = field(default=None, repr=False)
    native_shadow_snapshot: NativeShadowSnapshot | None = field(
        default=None, repr=False
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
        *,
        event_capture: RuntimeEventCaptureBuffer | None = None,
        native_hot_path_ports: NativeHotPathPorts | None = None,
    ) -> "RuntimeApp":
        """Convenience constructor for tests and future boot code."""

        registry = ServiceRegistry(services)
        return cls(
            config=config,
            services=registry,
            event_capture=event_capture,
            native_hot_path_ports=native_hot_path_ports,
        )

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
            (
                last_failure_at,
                last_failure_reason,
            ) = persistence_service.get_last_failure()
            snapshot["persistence"] = {
                "queue_depth": persistence_service.get_queue_depth(),
                "high_watermark": persistence_service.get_high_watermark(),
                "persisted_counts": persistence_service.get_persisted_counts(),
                "failure_counts": persistence_service.get_failure_counts(),
                "last_failure_at": (
                    last_failure_at.isoformat() if last_failure_at is not None else None
                ),
                "last_failure_reason": last_failure_reason,
            }
        snapshot["event_bus_depth"] = self.event_bus.qsize()
        snapshot["captured_at"] = datetime.now(timezone.utc).isoformat()
        return snapshot

    def get_native_shadow_report(self) -> dict[str, object] | None:
        """Return a read-only native-shadow comparison report for operators."""

        native_ports = self.native_hot_path_ports
        if native_ports is None:
            return None
        bridge = NativeShadowBridge(native_ports=native_ports)

        projection = build_runtime_projection(
            runtime_mode=self.config.runtime_mode,
            runtime_is_running=self.is_running,
            runtime_start_status=("running" if self.is_running else "configured"),
            position_source=self.position_service,
            execution_source=self.execution_service,
        )
        native_snapshot = self.native_shadow_snapshot
        if native_snapshot is None:
            return {
                "native_shadow_enabled": True,
                "native_shadow_status": "awaiting_native_snapshot",
                "native_shadow_is_match": True,
                "native_shadow_mismatch_count": 0,
                "native_shadow_mismatches": (),
                "native_shadow_summary": (
                    "Native shadow is enabled; awaiting native snapshot."
                ),
            }

        comparison = bridge.compare(projection, native_snapshot)
        return {
            "native_shadow_enabled": True,
            "native_shadow_status": ("match" if comparison.is_match else "divergent"),
            "native_shadow_is_match": comparison.is_match,
            "native_shadow_mismatch_count": len(comparison.mismatches),
            "native_shadow_mismatches": comparison.mismatches,
            "native_shadow_summary": (
                "Native shadow matches Python runtime."
                if comparison.is_match
                else "Native shadow diverges from Python runtime."
            ),
        }

    def get_recovery_report_snapshot(self) -> dict[str, object] | None:
        """Return the latest bounded runtime recovery report for operators."""

        report = self.recovery_report
        if report is None:
            return None

        snapshot = asdict(report)
        snapshot["issue_count"] = len(report.issues)
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
        event_capture: RuntimeEventCaptureBuffer | None = None,
        native_hot_path_ports: NativeHotPathPorts | None = None,
    ) -> "RuntimeApp":
        """Build the Phase 4 runtime shell with risk, execution, and persistence."""

        app = cls(
            config=config,
            event_capture=event_capture,
            native_hot_path_ports=native_hot_path_ports,
        )
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
            event_capture=app.event_capture,
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
        trade_store: object | None = None,
        market_data_bootstrap_gateway: MarketDataBootstrapGateway | None = None,
        user_stream_gateway: UserStreamGateway | None = None,
        max_open_positions: int = 0,
        kill_switch_enabled: bool = False,
        event_capture: RuntimeEventCaptureBuffer | None = None,
        native_hot_path_ports: NativeHotPathPorts | None = None,
    ) -> "RuntimeApp":
        """Build the Phase 5 runtime shell with market-data and signal wiring."""

        app = cls.build_phase4(
            config,
            execution_gateway=execution_gateway,
            runtime_store=runtime_store,
            user_stream_gateway=user_stream_gateway,
            max_open_positions=max_open_positions,
            kill_switch_enabled=kill_switch_enabled,
            event_capture=event_capture,
            native_hot_path_ports=native_hot_path_ports,
        )
        app.signal_event_source = asyncio.Queue()
        app.market_data_service = MarketDataService(
            name="market-data",
            gateway=market_data_gateway,
            runtime_config=config,
            event_bus=app.event_bus,
            bootstrap_gateway=market_data_bootstrap_gateway,
            event_sinks=(app.signal_event_source,),
            telemetry_service=app.telemetry_service,
        )
        app.signal_service = SignalService(
            name="signal",
            strategy=strategy,
            event_bus=app.event_bus,
            event_source=app.signal_event_source,
            market_state_source=app.market_data_service,
            position_source=app.position_service,
            risk_service=app.risk_service,
            telemetry_service=app.telemetry_service,
            supported_timeframes=config.kline_intervals,
        )

        app.register_service("market-data", app.market_data_service)
        app.register_service("signal", app.signal_service)
        if trade_store is not None and config.runtime_mode.runtime_enabled:
            app.recovery_service = RestartRecoveryService(
                name="recovery",
                trade_store=trade_store,
                execution_gateway=execution_gateway,
                execution_service=app.execution_service,
                position_service=app.position_service,
                reapply_native_protection=config.runtime_mode.owns_exits,
            )
            app.register_service("recovery", app.recovery_service)
        return app

    async def start(self) -> None:
        if self.is_running:
            return
        services_started = False
        previous_entries_paused: bool | None = None
        try:
            await self._initialize_runtime_store()
            if self.risk_service is not None:
                previous_entries_paused = self.risk_service.entries_paused
                self.risk_service.set_entries_paused(True)
            await self.services.start_all()
            services_started = True
            await self._recover_runtime_state()
        except Exception:
            if services_started:
                try:
                    await self.services.stop_all()
                except Exception:
                    logging.getLogger(__name__).warning(
                        "runtime service shutdown failed during startup recovery",
                        exc_info=True,
                    )
            await self._close_runtime_store()
            self.is_running = False
            raise
        finally:
            if previous_entries_paused is not None and self.risk_service is not None:
                self.risk_service.set_entries_paused(previous_entries_paused)
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

    async def _recover_runtime_state(self) -> None:
        recovery_service = self.recovery_service
        if recovery_service is None:
            self.recovery_report = None
            return

        report = await recovery_service.recover()
        self.recovery_report = report

        persistence_service = self.persistence_service
        if persistence_service is None:
            return

        enqueued = persistence_service.enqueue_recovery_report(report)
        if not enqueued:
            logging.getLogger(__name__).warning(
                "recovery report dropped before persistence: queue full"
            )
            return

        try:
            await persistence_service.wait_until_drained(timeout_seconds=1.0)
        except Exception as exc:  # pragma: no cover - defensive logging
            logging.getLogger(__name__).warning(
                "recovery report persistence failed: %s", exc
            )
