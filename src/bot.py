import asyncio
import json
import os
from typing import Dict, List
from datetime import datetime
import signal
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from exchange.binance_client import BinanceClient
from bot_logging.logger import setup_logging
from risk_management import RiskManager
from advanced_risk_management import AdvancedRiskManager
from notifications.telegram_bot import TelegramNotifier
from strategies.momentum_strategy import MomentumStrategy
from strategies.base_strategy import StrategyManager
from persistence.trade_store import TradeStore
from performance.metrics_tracker import PerformanceMetricsTracker
from monitoring.reconciliation import ReconciliationManager
from operations import ModelTrainingManager
from operations.alerts import OperationalAlertState
from operations.alert_evaluator import OperationalAlertEvaluator
from operations.alerts import (
    ALERT_SEVERITY_CRITICAL,
    HALT_STATE_LEGACY_CIRCUIT_BREAKER,
    HALT_STATE_NONE,
    HALT_STATE_RUNTIME_KILL_SWITCH,
    OperationsConfig,
)
from trading.position_manager import PositionManager
from trading.opportunity_scanner import OpportunityScanner
from runtime.config import RuntimeConfig
from runtime.mode import RuntimeMode
from runtime.projection import build_runtime_projection


class AkibaCryptoBot:
    CYCLE_INTERVAL_SECONDS = 60
    RECONCILIATION_CYCLE_INTERVAL = 60
    PORTFOLIO_HISTORY_LIMIT = 1000
    EQUITY_CURVE_LIMIT = 5000
    CLEANUP_STEP_TIMEOUT_SECONDS = 15
    CLEANUP_NETWORK_TIMEOUT_SECONDS = 5

    def __init__(self, config_path: str = "config/config.json"):
        self.config = self.load_config(config_path)
        self.logger = setup_logging(self.config.get("logging", {}))

        self.exchange = BinanceClient(
            sandbox=self.config["exchange"].get("sandbox", True)
        )

        # Use production Binance for market data when trading in sandbox mode,
        # because the testnet returns too few OHLCV candles for indicator calc.
        if self.config["exchange"].get("sandbox", True):
            self.data_exchange = BinanceClient(api_key="", secret_key="", sandbox=False)
            self.logger.info(
                "Dual-client mode: sandbox for trading, production Binance for market data"
            )
        else:
            self.data_exchange = self.exchange

        self.risk_manager = RiskManager(self.config.get("risk_management", {}))
        self.advanced_risk = AdvancedRiskManager(self.config)
        self.telegram = (
            TelegramNotifier()
            if self.config.get("notifications", {}).get("telegram_enabled")
            else None
        )

        # Setup Telegram bot commands if enabled
        if self.telegram and self.telegram.enabled:
            self.telegram.setup_bot_commands(self)

        # Initialize strategy manager
        self.strategy_manager = StrategyManager(self.config)

        # Load strategies based on configuration
        self._load_strategies()

        # Trade persistence (SQLite)
        db_path = self.config.get("persistence", {}).get("db_path", "data/trades.db")
        self.trade_store = TradeStore(db_path)
        self.training_manager = ModelTrainingManager(self, config_path=config_path)
        self.active_trades: Dict[str, Dict] = {}

        # Created in start() once we have the real wallet balance
        self.performance_tracker = None

        # Monitoring
        self.reconciliation = ReconciliationManager(self)

        # Trading subsystems
        self.position_manager = PositionManager(self)
        self.opportunity_scanner = OpportunityScanner(self)

        # Runtime supervisor (Phase 6): inactive unless explicitly configured.
        self.runtime_config = self._load_runtime_config()
        self.operations_config = OperationsConfig.from_mapping(
            self.config.get("operations", {})
        )
        self.alert_evaluator = OperationalAlertEvaluator(self.operations_config)
        self.runtime_handle = None
        self.runtime_app = None
        self._runtime_start_status = (
            "configured" if self.runtime_enabled else "disabled"
        )
        self._runtime_start_started_at = None

        self.is_running = False
        self.trading_paused = False
        self._operational_alert_state = OperationalAlertState()
        self.start_time = datetime.now()
        self.last_update = datetime.now()
        self.session_id = None  # Set when session is recorded to DB
        self._background_tasks: set = set()  # Prevents GC of fire-and-forget tasks

        # Portfolio tracking for circuit breakers
        self.portfolio_value_history = []
        self.price_data_cache = {}  # Cache for correlation analysis
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self._cached_balance = None  # Refreshed once per trading cycle
        self.pair_cooldowns: Dict[str, datetime] = (
            {}
        )  # symbol -> cooldown-expiry datetime
        self._stop_event = asyncio.Event()
        self._cleanup_future: asyncio.Future[None] | None = None

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    @staticmethod
    def load_config(config_path: str) -> Dict:
        with open(config_path, "r") as f:
            return json.load(f)

    def _load_runtime_config(self) -> RuntimeConfig | None:
        runtime_section = self.config.get("runtime")
        if not isinstance(runtime_section, dict):
            return None
        if not any(
            key in runtime_section
            for key in (
                "deployment_profile",
                "runtime_mode",
                "shadow_mode",
            )
        ):
            return None
        return RuntimeConfig.from_mapping(self.config)

    def get_runtime_visibility_info(self) -> Dict[str, object]:
        """Return the selected deployment profile and effective runtime ownership."""

        runtime_config = self.runtime_config
        configured_deployment_profile = None
        if runtime_config is not None:
            configured_deployment_profile = runtime_config.deployment_profile_name

        return {
            "configured_deployment_profile": configured_deployment_profile,
            "effective_runtime_mode": self.runtime_mode.value,
            "effective_runtime_enabled": self.runtime_enabled,
            "effective_runtime_owns_entries": self.runtime_owns_entries,
            "effective_runtime_owns_exits": self.runtime_owns_exits,
            "runtime_mode_source": (
                "deployment_profile"
                if configured_deployment_profile is not None
                else "runtime_mode" if runtime_config is not None else "legacy_default"
            ),
        }

    @property
    def runtime_mode(self) -> RuntimeMode:
        if self.runtime_config is None:
            return RuntimeMode.LEGACY
        return self.runtime_config.runtime_mode

    @property
    def runtime_enabled(self) -> bool:
        return self.runtime_mode.runtime_enabled

    @property
    def runtime_owns_entries(self) -> bool:
        return self.runtime_mode.owns_entries

    @property
    def runtime_owns_exits(self) -> bool:
        return self.runtime_mode.owns_exits

    @property
    def runtime_is_running(self) -> bool:
        runtime_app = self.runtime_app
        return bool(
            runtime_app is not None and getattr(runtime_app, "is_running", False)
        )

    @property
    def runtime_start_status(self) -> str:
        if not self.runtime_enabled:
            return "disabled"
        if self.runtime_is_running:
            return "running"
        return getattr(self, "_runtime_start_status", "configured")

    @property
    def runtime_start_pending(self) -> bool:
        return self.runtime_start_status in {"configured", "starting"}

    def _build_runtime_handle(self):
        if self.runtime_config is None or not self.runtime_enabled:
            return None

        from adapters.binance.bootstrap_gateway import BinanceBootstrapGateway
        from adapters.binance.execution_gateway import BinanceExecutionGateway
        from adapters.binance.user_data_gateway import BinanceUserDataGateway
        from adapters.binance.ws_gateway import BinanceWebSocketGateway
        from adapters.sqlite.runtime_store import SQLiteRuntimeStore
        from runtime.app import RuntimeApp
        from runtime.shadow import build_shadow_runtime
        from strategies.runtime_adapter import LegacyStrategyRuntimeAdapter

        market_data_gateway = BinanceWebSocketGateway(
            api_key=self.exchange.api_key or "",
            api_secret=self.exchange.secret_key or "",
            testnet=self.runtime_config.sandbox_market_data,
        )

        if self.runtime_config.runtime_is_shadow:
            return build_shadow_runtime(
                self.runtime_config,
                gateway=market_data_gateway,
            )

        active_strategy = self.strategy_manager.get_active_strategy()
        if active_strategy is None:
            raise RuntimeError("runtime-enabled bot requires an active strategy")

        runtime_strategy = LegacyStrategyRuntimeAdapter(
            active_strategy,
            entry_quantity=0.0,
            quantity_resolver=self._resolve_runtime_entry_quantity,
        )
        runtime_store = SQLiteRuntimeStore(
            db_path=self.config.get("persistence", {}).get(
                "runtime_db_path", "data/runtime.db"
            )
        )
        execution_gateway = BinanceExecutionGateway(client=self.exchange)
        user_stream_gateway = BinanceUserDataGateway(
            api_key=self.exchange.api_key or "",
            api_secret=self.exchange.secret_key or "",
            testnet=self.runtime_config.sandbox_execution,
        )
        bootstrap_gateway = BinanceBootstrapGateway(client=self.data_exchange)
        return RuntimeApp.build_phase5(
            self.runtime_config,
            market_data_gateway=market_data_gateway,
            execution_gateway=execution_gateway,
            runtime_store=runtime_store,
            strategy=runtime_strategy,
            trade_store=self.trade_store,
            market_data_bootstrap_gateway=bootstrap_gateway,
            user_stream_gateway=user_stream_gateway,
            max_open_positions=self.config.get("trading", {}).get("max_open_trades", 0),
            kill_switch_enabled=self.circuit_breaker_active,
        )

    def _resolve_runtime_entry_quantity(self, snapshot, signal) -> float:
        del signal
        stake_amount = float(self.config.get("trading", {}).get("stake_amount", 0.0))
        if stake_amount <= 0:
            return 0.0
        current_price = snapshot.last_price
        if current_price is None and snapshot.closed_candles:
            current_price = snapshot.closed_candles[-1].close
        if current_price is None or current_price <= 0:
            return 0.0
        return stake_amount / current_price

    async def _start_runtime_if_enabled(self) -> None:
        if not self.runtime_enabled or self.runtime_handle is not None:
            return

        self._runtime_start_status = "starting"
        self._runtime_start_started_at = datetime.now().astimezone()
        runtime_handle = self._build_runtime_handle()
        if runtime_handle is None:
            self._runtime_start_status = "failed"
            return

        self.runtime_handle = runtime_handle
        self.runtime_app = getattr(runtime_handle, "app", runtime_handle)
        self.logger.info(
            "Starting runtime supervisor in %s mode",
            self.runtime_mode.value,
        )
        try:
            await runtime_handle.start()
        except Exception:
            self.runtime_handle = None
            self.runtime_app = None
            self._runtime_start_status = "failed"
            raise
        self._runtime_start_status = "running"
        self._sync_runtime_entry_pause()

    def _sync_runtime_entry_pause(self) -> None:
        runtime_app = self.runtime_app
        if runtime_app is None:
            return
        risk_service = getattr(runtime_app, "risk_service", None)
        if risk_service is None or not hasattr(risk_service, "set_entries_paused"):
            return
        risk_service.set_entries_paused(self._entries_blocked())

    def _entries_blocked(self) -> bool:
        return self.trading_paused or self.halt_state != HALT_STATE_NONE

    def _log_runtime_visibility(self) -> None:
        visibility = self.get_runtime_visibility_info()
        self.logger.info(
            "Runtime deployment profile=%s effective_runtime_mode=%s "
            "effective_runtime_enabled=%s effective_runtime_owns_entries=%s "
            "effective_runtime_owns_exits=%s runtime_mode_source=%s",
            visibility["configured_deployment_profile"],
            visibility["effective_runtime_mode"],
            visibility["effective_runtime_enabled"],
            visibility["effective_runtime_owns_entries"],
            visibility["effective_runtime_owns_exits"],
            visibility["runtime_mode_source"],
        )
        if (
            self.runtime_config is None
            or self.runtime_config.deployment_profile is None
        ):
            return
        if not self.runtime_config.deployment_profile.is_live:
            return

        live_stage = (
            "legacy"
            if not self.runtime_enabled
            else (
                "canary"
                if self.runtime_config.deployment_profile.is_canary
                else "primary"
            )
        )
        self.logger.info(
            "Live runtime rollout profile=%s stage=%s allow_live_runtime=%s",
            self.runtime_config.deployment_profile.value,
            live_stage,
            self.runtime_config.allow_live_runtime,
        )

    def _validate_runtime_startup(self) -> None:
        if self.runtime_config is None:
            return
        self.runtime_config.validate_startup(
            exchange_sandbox=self.config["exchange"].get("sandbox", True)
        )

    async def _stop_runtime(self) -> None:
        runtime_handle = self.runtime_handle
        self.runtime_handle = None
        self.runtime_app = None
        if runtime_handle is None:
            return
        await self._run_cleanup_step(
            "runtime.stop",
            runtime_handle.stop(),
            timeout=self.CLEANUP_NETWORK_TIMEOUT_SECONDS,
        )
        self._runtime_start_status = "stopped"
        self._runtime_start_started_at = None

    def signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.stop()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        self.logger.info("Starting AkibaCryptoBot...")
        runtime_config = self._load_runtime_config()
        if runtime_config is not None:
            self.runtime_config = runtime_config
        self._runtime_start_status = (
            "configured" if self.runtime_enabled else "disabled"
        )
        self._runtime_start_started_at = (
            datetime.now().astimezone() if self.runtime_enabled else None
        )
        self._log_runtime_visibility()
        self._validate_runtime_startup()
        self.is_running = True
        self._stop_event.clear()
        if self._cleanup_future is not None and self._cleanup_future.done():
            self._cleanup_future = None

        try:
            # Initialize trade persistence and reload open positions
            await self.trade_store.initialize()
            await self.training_manager.initialize()
            self.active_trades = await self.trade_store.load_open_trades()

            # Record this bot session in the database
            strategy = self.strategy_manager.active_strategy or ""
            trading_mode = (
                "paper" if self.config["exchange"].get("sandbox", True) else "live"
            )
            self.session_id = await self.trade_store.start_session(
                strategy=strategy, trading_mode=trading_mode
            )

            # Re-sync risk manager with persisted trades so counters and
            # trailing stops are correct after a restart.
            for symbol, trade in self.active_trades.items():
                self.risk_manager.add_position(
                    symbol=symbol,
                    side=trade["side"],
                    amount=trade["amount"],
                    entry_price=trade["entry_price"],
                    stop_loss=trade.get("stop_loss", 0),
                    take_profit=trade.get("take_profit", 0),
                    order_id=trade.get("order_id", ""),
                )
                # Restore trailing stop if it was previously ratcheted
                if (
                    trade.get("trailing_stop_price")
                    and symbol in self.risk_manager.positions
                ):
                    self.risk_manager.positions[symbol]["trailing_stop_price"] = trade[
                        "trailing_stop_price"
                    ]
            if self.active_trades:
                self.logger.info(
                    f"Risk manager re-synced with {len(self.active_trades)} persisted trade(s)"
                )

            # Initialize exchange connection
            balance = await self.exchange.get_balance()
            self.logger.info("Successfully connected to exchange")

            # Create performance tracker with real wallet balance
            usdt_info = balance.get("USDT", {})
            initial_balance = usdt_info.get("free", 0) + usdt_info.get("used", 0)
            self.performance_tracker = PerformanceMetricsTracker(
                initial_balance=initial_balance
            )

            # Hydrate performance tracker with historical closed trades
            closed_trades = await self.trade_store.load_closed_trades()
            self.performance_tracker.hydrate_from_db(closed_trades)

            # Hydrate equity curve from persistent snapshots
            portfolio_history = await self.trade_store.load_portfolio_history(
                limit=self.EQUITY_CURVE_LIMIT
            )
            self.performance_tracker.hydrate_equity_curve(portfolio_history)

            # Seed performance tracker with persisted open trades
            for symbol, trade in self.active_trades.items():
                self.performance_tracker.record_trade_entry(
                    symbol=symbol,
                    side=trade["side"],
                    amount=trade["amount"],
                    entry_price=trade["entry_price"],
                    strategy=self.strategy_manager.active_strategy or "unknown",
                )

            # Verify production data feed when running in dual-client mode
            if self.data_exchange is not self.exchange:
                await self.data_exchange.get_ticker(self.config["trading"]["pairs"][0])
                self.logger.info(
                    "Successfully connected to production Binance for market data"
                )

            await self._start_runtime_if_enabled()
            alert_task = asyncio.create_task(self._run_operational_alert_loop())
            self._background_tasks.add(alert_task)
            alert_task.add_done_callback(self._background_tasks.discard)

            # Perform initial reconciliation check
            await self.reconciliation.reconcile()

            if self.telegram:
                await self.telegram.send_message(
                    "🤖 AkibaCryptoBot started successfully!"
                )
                # Start Telegram bot polling in background
                if self.telegram.application:
                    await self.telegram.application.initialize()
                    for coro in (
                        self.telegram.application.start(),
                        self.telegram.application.updater.start_polling(),
                    ):
                        task = asyncio.create_task(coro)
                        self._background_tasks.add(task)
                        task.add_done_callback(self._background_tasks.discard)

            # Main trading loop
            cycle_count = 0
            while self.is_running:
                try:
                    await self.trading_cycle()
                    cycle_count += 1

                    # Run reconciliation every N cycles (~1 hour)
                    if cycle_count % self.RECONCILIATION_CYCLE_INTERVAL == 0:
                        await self.reconciliation.reconcile()

                    if await self._wait_for_stop(self.CYCLE_INTERVAL_SECONDS):
                        break
                except Exception as e:
                    self.logger.error(f"Error in trading cycle: {e}")
                    if self.telegram:
                        await self.telegram.send_message(
                            f"❌ Trading cycle error: {str(e)}"
                        )
                    if await self._wait_for_stop(self.CYCLE_INTERVAL_SECONDS):
                        break

        except asyncio.CancelledError:
            self.logger.info("Trading loop cancelled, stopping bot...")
            self.stop()
            raise
        except Exception as e:
            self.logger.error(f"Critical error: {e}")
            if self.telegram:
                await self.telegram.send_message(f"🚨 Critical error: {str(e)}")
        finally:
            self.is_running = False
            await self.cleanup()

    def stop(self):
        if not self.is_running and self._stop_event.is_set():
            return
        self.logger.info("Stopping bot...")
        self.is_running = False
        self._stop_event.set()

    async def cleanup(self):
        existing_cleanup = self._cleanup_future
        if existing_cleanup is not None:
            await asyncio.shield(existing_cleanup)
            return

        cleanup_future = asyncio.get_running_loop().create_future()
        self._cleanup_future = cleanup_future

        try:
            await self._cleanup_impl()
        except Exception as exc:
            if not cleanup_future.done():
                cleanup_future.set_exception(exc)
            raise
        else:
            if not cleanup_future.done():
                cleanup_future.set_result(None)

    async def _cleanup_impl(self):
        self.logger.info("Cleaning up resources...")
        self.is_running = False
        self._stop_event.set()

        cancel_order_steps = [
            self._run_cleanup_step(
                f"cancel_orders:{symbol}",
                self._cancel_orders_for_symbol(symbol),
                timeout=self.CLEANUP_NETWORK_TIMEOUT_SECONDS,
            )
            for symbol in tuple(self.active_trades)
        ]
        if cancel_order_steps:
            await asyncio.gather(*cancel_order_steps)

        # End the current bot session
        if self.session_id is not None:
            await self._run_cleanup_step(
                "end_session",
                self.trade_store.end_session(
                    self.session_id, exit_reason="clean_shutdown"
                ),
                timeout=self.CLEANUP_NETWORK_TIMEOUT_SECONDS,
            )
            self.session_id = None

        await self._cancel_background_tasks()

        if self.telegram:
            await self._shutdown_telegram_fast()

        close_steps = []

        await self._stop_runtime()

        if self.data_exchange and self.data_exchange is not self.exchange:
            close_steps.append(
                self._run_cleanup_step(
                    "data_exchange.close",
                    self.data_exchange.close(),
                    timeout=self.CLEANUP_NETWORK_TIMEOUT_SECONDS,
                )
            )

        if self.exchange:
            close_steps.append(
                self._run_cleanup_step(
                    "exchange.close",
                    self.exchange.close(),
                    timeout=self.CLEANUP_NETWORK_TIMEOUT_SECONDS,
                )
            )

        if close_steps:
            await asyncio.gather(*close_steps)

        await self._run_cleanup_step(
            "trade_store.close",
            self.trade_store.close(),
            timeout=self.CLEANUP_NETWORK_TIMEOUT_SECONDS,
        )

    async def _wait_for_stop(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _run_operational_alert_loop(self) -> None:
        interval = self.operations_config.evaluation_interval_seconds
        while self.is_running and not self._stop_event.is_set():
            try:
                await self.evaluate_operational_alerts()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.error("Operational alert evaluation failed: %s", exc)
            if await self._wait_for_stop(interval):
                break

    async def _run_cleanup_step(
        self, step_name: str, awaitable, timeout: float | None = None
    ) -> None:
        self.logger.debug(f"Cleanup step starting: {step_name}")
        try:
            await asyncio.wait_for(
                awaitable,
                timeout=timeout or self.CLEANUP_STEP_TIMEOUT_SECONDS,
            )
            self.logger.debug(f"Cleanup step completed: {step_name}")
        except asyncio.TimeoutError:
            self.logger.error(f"Cleanup step timed out: {step_name}")
        except Exception as e:
            self.logger.error(f"Cleanup step failed ({step_name}): {e}")

    async def _shutdown_telegram_fast(self) -> None:
        await self._run_cleanup_step(
            "telegram.send_stop_message",
            self.telegram.send_message("AkibaCryptoBot stopped"),
            timeout=self.CLEANUP_NETWORK_TIMEOUT_SECONDS,
        )

        application = getattr(self.telegram, "application", None)
        if application is None:
            return

        updater = getattr(application, "updater", None)
        if updater and getattr(updater, "running", False):
            await self._run_cleanup_step(
                "telegram.updater.stop",
                updater.stop(),
                timeout=self.CLEANUP_NETWORK_TIMEOUT_SECONDS,
            )

        if getattr(application, "running", False):
            await self._run_cleanup_step(
                "telegram.application.stop",
                application.stop(),
                timeout=self.CLEANUP_NETWORK_TIMEOUT_SECONDS,
            )

        await self._run_cleanup_step(
            "telegram.application.shutdown",
            application.shutdown(),
            timeout=self.CLEANUP_NETWORK_TIMEOUT_SECONDS,
        )

    async def _shutdown_telegram(self) -> None:
        await self._run_cleanup_step(
            "telegram.send_stop_message",
            self.telegram.send_message("🛑 AkibaCryptoBot stopped"),
        )

        application = getattr(self.telegram, "application", None)
        if application is None:
            return

        updater = getattr(application, "updater", None)
        if updater and getattr(updater, "running", False):
            await self._run_cleanup_step("telegram.updater.stop", updater.stop())

        if getattr(application, "running", False):
            await self._run_cleanup_step(
                "telegram.application.stop", application.stop()
            )

        await self._run_cleanup_step(
            "telegram.application.shutdown",
            application.shutdown(),
        )

    async def _cancel_background_tasks(self) -> None:
        pending_tasks = [task for task in self._background_tasks if not task.done()]
        if not pending_tasks:
            self._background_tasks.clear()
            return

        for task in pending_tasks:
            task.cancel()

        for task in pending_tasks:
            try:
                await asyncio.wait_for(task, timeout=self.CLEANUP_STEP_TIMEOUT_SECONDS)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                continue
            except Exception as e:
                self.logger.error(f"Background task failed during cleanup: {e}")

        self._background_tasks.clear()

    async def _cancel_orders_for_symbol(self, symbol: str):
        """Cancel all open orders for a symbol. Best-effort — errors are logged."""
        try:
            open_orders = await self.exchange.get_open_orders(symbol)
            for order in open_orders:
                try:
                    await self.exchange.cancel_order(order["id"], symbol)
                    self.logger.info(
                        f"Cancelled order {order['id']} for {symbol} on shutdown"
                    )
                except Exception as e:
                    self.logger.error(
                        f"Failed to cancel order {order['id']} for {symbol}: {e}"
                    )
        except Exception as e:
            self.logger.error(
                f"Failed to fetch open orders for {symbol} during cleanup: {e}"
            )

    # ------------------------------------------------------------------
    # Trading cycle
    # ------------------------------------------------------------------

    async def trading_cycle(self):
        self.logger.debug("Starting trading cycle...")

        # Refresh balance cache once per cycle to avoid redundant REST calls
        self._cached_balance = await self.exchange.get_balance()

        # Update market data
        await self.update_market_data()

        # Update portfolio value for circuit breaker tracking
        await self.update_portfolio_value()

        # Check circuit breakers for extreme market conditions
        should_halt, reason = self.advanced_risk.check_circuit_breakers(
            self.price_data_cache, self.portfolio_value_history
        )

        if should_halt:
            if not self.circuit_breaker_active:
                self.circuit_breaker_active = True
                self.circuit_breaker_reason = reason
                self.logger.warning(f"🚨 CIRCUIT BREAKER ACTIVATED: {reason}")
                if self.telegram:
                    await self.telegram.send_message(
                        f"🚨 CIRCUIT BREAKER ACTIVATED\n\n{reason}\n\n"
                        f"New positions are blocked until conditions normalize."
                    )
        else:
            if self.circuit_breaker_active:
                self.circuit_breaker_active = False
                self.logger.info(
                    "✅ Circuit breaker deactivated - conditions normalized"
                )
                if self.telegram:
                    await self.telegram.send_message(
                        "✅ Circuit breaker deactivated - trading resumed"
                    )

        # Check existing positions only while the legacy path still owns exits.
        if self.runtime_owns_exits:
            self.logger.debug(
                "Skipping legacy position management - runtime owns exits"
            )
        else:
            await self.position_manager.manage_positions()

        # Update correlations for risk management (every cycle)
        if len(self.price_data_cache) >= 2:
            self.advanced_risk.calculate_correlations(self.price_data_cache)

        # Look for new trading opportunities only when the legacy path still
        # owns entries and the circuit breaker is inactive.
        if self.runtime_owns_entries:
            self.logger.debug("Skipping legacy opportunity scan - runtime owns entries")
        elif self._entries_blocked():
            self.logger.debug(
                "Skipping opportunity scan - entries are blocked (%s)",
                self.entries_blocked_reason or "manual_pause",
            )
        elif not self.circuit_breaker_active:
            await self.opportunity_scanner.scan_opportunities()
        else:
            self.logger.debug("Skipping opportunity scan - circuit breaker active")

        # Update last cycle time
        self.last_update = datetime.now()

    async def update_market_data(self):
        for pair in self.config["trading"]["pairs"]:
            try:
                ticker = await self.data_exchange.get_ticker(pair)
                self.logger.debug(f"Updated {pair}: {ticker['last']}")

                # Cache OHLCV data for circuit breaker and correlation analysis
                ohlcv_data = await self.data_exchange.get_ohlcv(
                    pair,
                    self.config["trading"]["timeframes"]["primary"],
                    100,  # Last 100 candles for analysis
                )
                if ohlcv_data is not None and not ohlcv_data.empty:
                    self.price_data_cache[pair] = ohlcv_data

            except Exception as e:
                self.logger.error(f"Error updating market data for {pair}: {e}")

    async def update_portfolio_value(self):
        """Update portfolio value history for circuit breaker tracking."""
        try:
            balance = self._cached_balance or await self.exchange.get_balance()
            total_value = 0.0

            # Add USDT balance
            usdt_balance = balance.get("USDT", {}).get("free", 0) + balance.get(
                "USDT", {}
            ).get("used", 0)
            total_value += usdt_balance

            # Add value of open positions (approximation)
            for symbol in self.active_trades:
                trade = self.active_trades[symbol]
                ticker = await self.data_exchange.get_ticker(symbol)
                position_value = trade["amount"] * ticker["last"]
                total_value += position_value

            self.portfolio_value_history.append(total_value)
            if len(self.portfolio_value_history) > self.PORTFOLIO_HISTORY_LIMIT:
                self.portfolio_value_history.pop(0)

            # Update performance tracker's live equity curve
            self.performance_tracker.update_equity_curve(total_value)

            # Persist snapshot to database
            await self.trade_store.insert_portfolio_snapshot(total_value)

        except Exception as e:
            self.logger.error(f"Error updating portfolio value: {e}")

    # ------------------------------------------------------------------
    # Delegation methods — keep the public interface stable for API
    # routes, Telegram commands, ReconciliationManager, and tests.
    # ------------------------------------------------------------------

    async def close_position(self, symbol: str, reason: str):
        await self.position_manager.close_position(symbol, reason)

    async def manage_positions(self):
        if self.runtime_owns_exits:
            self.logger.debug(
                "Skipping legacy manage_positions call - runtime owns exits"
            )
            return
        await self.position_manager.manage_positions()

    async def place_trade(
        self, symbol: str, side: str, amount: float, entry_price: float
    ):
        await self.opportunity_scanner.place_trade(symbol, side, amount, entry_price)

    async def scan_opportunities(self):
        if self.runtime_owns_entries:
            self.logger.debug(
                "Skipping legacy scan_opportunities call - runtime owns entries"
            )
            return
        await self.opportunity_scanner.scan_opportunities()

    async def _process_pair(self, pair: str):
        if self.runtime_owns_entries:
            self.logger.debug(
                "Skipping legacy _process_pair call for %s - runtime owns entries",
                pair,
            )
            return
        await self.opportunity_scanner._process_pair(pair)

    async def _check_spread(self, symbol: str) -> bool:
        return await self.opportunity_scanner._check_spread(symbol)

    def validate_trading_conditions(self, symbol: str, signal_obj) -> bool:
        return self.opportunity_scanner.validate_trading_conditions(symbol, signal_obj)

    # ------------------------------------------------------------------
    # Info accessors (Telegram + API routes)
    # ------------------------------------------------------------------

    def get_status_info(self) -> Dict:
        """Get bot status information for Telegram commands."""
        uptime = datetime.now() - self.start_time
        visibility_info = self.get_runtime_visibility_info()
        alert_state = self.get_operational_alert_state()
        critical_alert_count = sum(
            1
            for alert in alert_state["operational_alerts"]
            if alert["severity"] == ALERT_SEVERITY_CRITICAL
        )
        return {
            "active_trades": len(self.active_trades),
            "daily_pnl": 0.0,
            "uptime": str(uptime),
            "is_running": self.is_running,
            "trading_paused": self.trading_paused,
            "runtime_mode": self.runtime_mode.value,
            "configured_deployment_profile": visibility_info[
                "configured_deployment_profile"
            ],
            "effective_runtime_mode": visibility_info["effective_runtime_mode"],
            "effective_runtime_enabled": visibility_info["effective_runtime_enabled"],
            "effective_runtime_owns_entries": visibility_info[
                "effective_runtime_owns_entries"
            ],
            "effective_runtime_owns_exits": visibility_info[
                "effective_runtime_owns_exits"
            ],
            "runtime_mode_source": visibility_info["runtime_mode_source"],
            "runtime_is_running": self.runtime_is_running,
            "runtime_start_status": self.runtime_start_status,
            "runtime_start_pending": self.runtime_start_pending,
            "runtime_owns_entries": self.runtime_owns_entries,
            "runtime_owns_exits": self.runtime_owns_exits,
            "runtime_telemetry": self.get_runtime_telemetry_info(),
            "native_shadow_report": self.get_native_shadow_report(),
            "operational_alerts": alert_state["operational_alerts"],
            "halt_state": alert_state["halt_state"],
            "entries_blocked_reason": alert_state["entries_blocked_reason"],
            "active_critical_alert_count": critical_alert_count,
        }

    def get_runtime_projection(self):
        runtime_app = self.runtime_app
        return build_runtime_projection(
            runtime_mode=self.runtime_mode,
            runtime_is_running=self.runtime_is_running,
            runtime_start_status=self.runtime_start_status,
            position_source=(
                None if runtime_app is None else runtime_app.position_service
            ),
            execution_source=(
                None if runtime_app is None else runtime_app.execution_service
            ),
        )

    def get_native_shadow_report(self):
        runtime_app = self.runtime_app
        if runtime_app is None:
            return None

        report_getter = getattr(runtime_app, "get_native_shadow_report", None)
        if not callable(report_getter):
            return None
        return report_getter()

    def get_runtime_telemetry_info(self):
        runtime_app = self.runtime_app
        if runtime_app is None:
            return None
        telemetry_getter = getattr(runtime_app, "get_telemetry_snapshot", None)
        if not callable(telemetry_getter):
            return None
        return telemetry_getter()

    def get_runtime_recovery_info(self):
        runtime_app = self.runtime_app
        if runtime_app is None:
            return None
        recovery_getter = getattr(runtime_app, "get_recovery_report_snapshot", None)
        if not callable(recovery_getter):
            return None
        return recovery_getter()

    @property
    def operational_alerts(self):
        return tuple(self._operational_alert_state.operational_alerts)

    @property
    def halt_state(self) -> str:
        return self._operational_alert_state.halt_state

    @property
    def entries_blocked_reason(self) -> str | None:
        return self._operational_alert_state.entries_blocked_reason

    def get_operational_alert_state(self) -> Dict:
        return self._operational_alert_state.to_dict()

    def set_operational_alert_state(self, state: OperationalAlertState) -> None:
        self._operational_alert_state = state
        self._sync_runtime_entry_pause()

    async def evaluate_operational_alerts(self) -> OperationalAlertState:
        previous_state = self.get_operational_alert_state()
        projection = self.get_runtime_projection()
        projection_payload = {
            "runtime_is_running": projection.runtime_is_running,
            "runtime_is_healthy": projection.runtime_is_healthy,
            "runtime_start_pending": projection.runtime_start_pending,
            "runtime_start_status": projection.runtime_start_status,
            "runtime_start_started_at": self._runtime_start_started_at,
        }
        training_snapshot = await self.get_training_job_status()
        model_status = await self.get_model_status_info()
        evaluated = self.alert_evaluator.evaluate(
            runtime_projection=projection_payload,
            runtime_telemetry=self.get_runtime_telemetry_info(),
            native_shadow_report=self.get_native_shadow_report(),
            circuit_breaker_state={
                "active": self.circuit_breaker_active,
                "reason": self.circuit_breaker_reason,
            },
            model_status=model_status,
            training_job_status=training_snapshot.to_dict(),
        )

        if evaluated.entries_blocked_reason:
            halt_state = (
                HALT_STATE_RUNTIME_KILL_SWITCH
                if self.runtime_owns_entries
                else HALT_STATE_LEGACY_CIRCUIT_BREAKER
            )
            evaluated = OperationalAlertState(
                operational_alerts=evaluated.operational_alerts,
                halt_state=halt_state,
                entries_blocked_reason=evaluated.entries_blocked_reason,
            )

        self.set_operational_alert_state(evaluated)
        if self.telegram and hasattr(
            self.telegram, "send_operational_alert_transition"
        ):
            await self.telegram.send_operational_alert_transition(
                previous_state,
                evaluated.to_dict(),
            )
        return evaluated

    async def get_balance_info(self) -> Dict:
        """Get account balance for Telegram commands."""
        try:
            balance = await self.exchange.get_balance()
            valid_balances = {}
            for currency, data in balance.items():
                if isinstance(data, dict):
                    free = data.get("free", 0)
                    if free > 0:
                        valid_balances[currency] = free
            return valid_balances
        except Exception as e:
            self.logger.error(f"Error getting balance: {e}")
            return {}

    def get_active_trades(self) -> List[Dict]:
        """Get active trades for Telegram and API."""
        return list(self.active_trades.values())

    def get_config_info(self) -> Dict:
        """Get configuration info for Telegram commands."""
        return {
            "trading_mode": (
                "paper" if self.config["exchange"].get("sandbox", True) else "live"
            ),
            "max_position_size": self.config["risk_management"].get(
                "max_position_size", 0.05
            ),
            "stop_loss_percent": self.config["risk_management"].get(
                "stop_loss_percent", 0.02
            ),
            "take_profit_percent": self.config["risk_management"].get(
                "take_profit_percent", 0.04
            ),
            "max_open_trades": self.config["trading"].get("max_open_trades", 3),
            "pairs": self.config["trading"]["pairs"],
        }

    async def get_model_status_info(self) -> Dict:
        """Return the operator-facing FreqAI model and training snapshot."""

        active_strategy = self.strategy_manager.get_active_strategy()
        training_snapshot = await self.get_training_job_status()
        latest_success = await self.trade_store.get_latest_successful_training_job()

        if not hasattr(active_strategy, "strategies"):
            return {
                "freqai_active": False,
                "reason": "Active strategy is not an ensemble",
                "models": [],
                "last_success_at": (
                    latest_success.finished_at.isoformat()
                    if latest_success and latest_success.finished_at
                    else None
                ),
                "reload_status": (
                    training_snapshot.current_job.reload_status
                    if training_snapshot.current_job is not None
                    else (
                        training_snapshot.last_job.reload_status
                        if training_snapshot.last_job is not None
                        else None
                    )
                ),
                "current_job_id": (
                    training_snapshot.current_job.job_id
                    if training_snapshot.current_job is not None
                    else None
                ),
            }

        models = []
        for strategy, weight, name in active_strategy.strategies:
            if not hasattr(strategy, "model_trained"):
                continue

            model_info = {
                "name": name,
                "type": type(strategy).__name__,
                "weight": weight,
                "trained": getattr(strategy, "model_trained", False),
                "feature_count": len(getattr(strategy, "feature_columns", [])),
            }

            models_dir = self._resolve_models_dir()
            if os.path.exists(models_dir):
                model_files = [
                    f for f in os.listdir(models_dir) if f.endswith("_freqai.joblib")
                ]
                if model_files:
                    newest = max(
                        model_files,
                        key=lambda f: os.path.getmtime(os.path.join(models_dir, f)),
                    )
                    mtime = os.path.getmtime(os.path.join(models_dir, newest))
                    model_info["last_trained"] = datetime.fromtimestamp(
                        mtime
                    ).isoformat()
                    age_hours = (datetime.now().timestamp() - mtime) / 3600
                    model_info["age_hours"] = round(age_hours, 1)
                    model_info["stale"] = age_hours > 48

            if hasattr(strategy, "ml_model") and strategy.ml_model is not None:
                if hasattr(strategy.ml_model, "feature_importances_"):
                    cols = getattr(strategy, "feature_columns", [])
                    importances = strategy.ml_model.feature_importances_
                    if len(cols) == len(importances):
                        top = sorted(
                            zip(cols, importances), key=lambda x: x[1], reverse=True
                        )[:5]
                        model_info["top_features"] = [
                            {"name": n, "importance": round(float(v), 4)}
                            for n, v in top
                        ]

            models.append(model_info)

        any_trained = any(model["trained"] for model in models)
        return {
            "freqai_active": any_trained,
            "models": models,
            "last_success_at": (
                latest_success.finished_at.isoformat()
                if latest_success and latest_success.finished_at
                else None
            ),
            "reload_status": (
                training_snapshot.current_job.reload_status
                if training_snapshot.current_job is not None
                else (
                    training_snapshot.last_job.reload_status
                    if training_snapshot.last_job is not None
                    else None
                )
            ),
            "current_job_id": (
                training_snapshot.current_job.job_id
                if training_snapshot.current_job is not None
                else None
            ),
        }

    # ------------------------------------------------------------------
    # Strategy loading
    # ------------------------------------------------------------------

    def _load_strategies(self):
        """Load strategies based on configuration."""
        try:
            # Load default momentum strategy
            momentum_strategy = MomentumStrategy(self.config.get("strategy_config", {}))
            self.strategy_manager.add_strategy(momentum_strategy, "momentum")

            # Load strategy specified in config
            config_strategy = self.config["trading"].get("strategy", "momentum")
            if config_strategy and config_strategy != "momentum":
                self.strategy_manager.add_strategy_by_name(config_strategy, self.config)
                self.strategy_manager.set_active_strategy(config_strategy)
            else:
                self.strategy_manager.set_active_strategy("momentum")

            self.logger.info(
                f"Active strategy: {self.strategy_manager.active_strategy}"
            )

            # Load pre-trained FreqAI models from disk
            self._load_freqai_models()

        except Exception as e:
            self.logger.error(f"Error loading strategies: {e}")

    def _load_freqai_models(self):
        """
        Load pre-trained FreqAI models from the models/ directory.
        These are produced by scripts/train_models.py (run via GitHub Actions).
        """
        models_dir = self._resolve_models_dir()
        try:
            loaded = self.reload_freqai_models(models_dir=models_dir)
        except RuntimeError:
            self.logger.warning(
                "🧠 FreqAI is INACTIVE: no pre-trained models found. "
                "Ensemble will rely on momentum signals only."
            )
            return
        self.logger.info(f"🧠 FreqAI is ACTIVE: {loaded} pre-trained model(s) loaded")

    def _resolve_models_dir(self) -> str:
        models_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")
        if not os.path.exists(models_dir):
            return "models"
        return models_dir

    def _reset_freqai_models(self) -> None:
        active_strategy = self.strategy_manager.get_active_strategy()
        strategies = getattr(active_strategy, "strategies", ())
        for strategy, *_ in strategies:
            if hasattr(strategy, "ml_model"):
                strategy.ml_model = None
            if hasattr(strategy, "model_trained"):
                strategy.model_trained = False
            if hasattr(strategy, "is_model_trained"):
                strategy.is_model_trained = False
            if hasattr(strategy, "feature_columns"):
                strategy.feature_columns = []
            if hasattr(strategy, "model_expired"):
                strategy.model_expired = False
            if hasattr(strategy, "model_trained_at"):
                strategy.model_trained_at = None

    def reload_freqai_models(self, models_dir: str | None = None) -> int:
        """Reload FreqAI models from disk into the active strategy."""

        active_strategy = self.strategy_manager.get_active_strategy()
        if not hasattr(active_strategy, "load_models"):
            raise RuntimeError("freqai_not_available")

        resolved_models_dir = models_dir or self._resolve_models_dir()
        self._reset_freqai_models()
        loaded = active_strategy.load_models(resolved_models_dir)
        if loaded <= 0:
            raise RuntimeError("no_trained_models_found")
        return loaded

    # ------------------------------------------------------------------
    # Telegram control
    # ------------------------------------------------------------------

    def pause_trading(self):
        """Pause new entries while leaving the bot lifecycle running."""
        self.trading_paused = True
        self._sync_runtime_entry_pause()
        self.logger.info("Trading entries paused")

    def resume_trading(self):
        """Resume new entries after an operator pause."""
        self.trading_paused = False
        self._sync_runtime_entry_pause()
        self.logger.info("Trading entries resumed")

    async def get_training_job_status(self):
        return await self.training_manager.get_training_job_status()

    async def can_start_training(self) -> tuple[bool, str | None]:
        return await self.training_manager.can_start_training()

    async def enqueue_training_job(
        self,
        *,
        requested_by: str = "system",
        pairs: List[str] | None = None,
        candles: int = 1000,
        timeframe: str = "1h",
    ):
        return await self.training_manager.enqueue_training_job(
            requested_by=requested_by,
            pairs=pairs,
            candles=candles,
            timeframe=timeframe,
        )


async def main():
    bot = AkibaCryptoBot()
    try:
        await bot.start()
    except KeyboardInterrupt:
        bot.logger.info("Received keyboard interrupt")
    except Exception as e:
        bot.logger.error(f"Unexpected error: {e}")
    finally:
        bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
