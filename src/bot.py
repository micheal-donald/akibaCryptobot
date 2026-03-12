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
from trading.position_manager import PositionManager
from trading.opportunity_scanner import OpportunityScanner


class AkibaCryptoBot:
    CYCLE_INTERVAL_SECONDS = 60
    RECONCILIATION_CYCLE_INTERVAL = 60
    PORTFOLIO_HISTORY_LIMIT = 1000
    EQUITY_CURVE_LIMIT = 5000

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
        self.active_trades: Dict[str, Dict] = {}

        initial_balance = self.config.get("backtesting", {}).get(
            "initial_balance", 10000.0
        )
        self.performance_tracker = PerformanceMetricsTracker(
            initial_balance=initial_balance
        )

        # Monitoring
        self.reconciliation = ReconciliationManager(self)

        # Trading subsystems
        self.position_manager = PositionManager(self)
        self.opportunity_scanner = OpportunityScanner(self)

        self.is_running = False
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

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    @staticmethod
    def load_config(config_path: str) -> Dict:
        with open(config_path, "r") as f:
            return json.load(f)

    def signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.stop()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        self.logger.info("Starting AkibaCryptoBot...")
        self.is_running = True

        try:
            # Initialize trade persistence and reload open positions
            await self.trade_store.initialize()
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

            # Initialize exchange connection
            await self.exchange.get_balance()
            self.logger.info("Successfully connected to exchange")

            # Verify production data feed when running in dual-client mode
            if self.data_exchange is not self.exchange:
                await self.data_exchange.get_ticker(self.config["trading"]["pairs"][0])
                self.logger.info(
                    "Successfully connected to production Binance for market data"
                )

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

                    await asyncio.sleep(self.CYCLE_INTERVAL_SECONDS)
                except Exception as e:
                    self.logger.error(f"Error in trading cycle: {e}")
                    if self.telegram:
                        await self.telegram.send_message(
                            f"❌ Trading cycle error: {str(e)}"
                        )
                    await asyncio.sleep(self.CYCLE_INTERVAL_SECONDS)

        except Exception as e:
            self.logger.error(f"Critical error: {e}")
            if self.telegram:
                await self.telegram.send_message(f"🚨 Critical error: {str(e)}")
        finally:
            await self.cleanup()

    def stop(self):
        self.logger.info("Stopping bot...")
        self.is_running = False

    async def cleanup(self):
        self.logger.info("Cleaning up resources...")

        # Cancel open orders for all active trades (best-effort, errors are logged)
        for symbol in self.active_trades:
            await self._cancel_orders_for_symbol(symbol)

        # End the current bot session
        if self.session_id is not None:
            await self.trade_store.end_session(
                self.session_id, exit_reason="clean_shutdown"
            )

        # Close trade persistence DB
        await self.trade_store.close()

        if self.data_exchange and self.data_exchange is not self.exchange:
            await self.data_exchange.close()

        if self.exchange:
            await self.exchange.close()

        if self.telegram:
            try:
                await self.telegram.send_message("🛑 AkibaCryptoBot stopped")
                if self.telegram.application and self.telegram.application.running:
                    await self.telegram.application.stop()
                    await self.telegram.application.shutdown()
            except Exception as e:
                self.logger.error(f"Error stopping Telegram bot: {e}")

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

        # Check existing positions
        await self.position_manager.manage_positions()

        # Update correlations for risk management (every cycle)
        if len(self.price_data_cache) >= 2:
            self.advanced_risk.calculate_correlations(self.price_data_cache)

        # Look for new trading opportunities (only if circuit breaker is inactive)
        if not self.circuit_breaker_active:
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
        await self.position_manager.manage_positions()

    async def place_trade(
        self, symbol: str, side: str, amount: float, entry_price: float
    ):
        await self.opportunity_scanner.place_trade(symbol, side, amount, entry_price)

    async def scan_opportunities(self):
        await self.opportunity_scanner.scan_opportunities()

    async def _process_pair(self, pair: str):
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
        return {
            "active_trades": len(self.active_trades),
            "daily_pnl": 0.0,
            "uptime": str(uptime),
            "is_running": self.is_running,
        }

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
        active_strategy = self.strategy_manager.get_active_strategy()

        if hasattr(active_strategy, "load_models"):
            models_dir = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "models"
            )
            if not os.path.exists(models_dir):
                models_dir = "models"  # fallback to relative path

            loaded = active_strategy.load_models(models_dir)
            if loaded > 0:
                self.logger.info(
                    f"🧠 FreqAI is ACTIVE: {loaded} pre-trained model(s) loaded"
                )
            else:
                self.logger.warning(
                    "🧠 FreqAI is INACTIVE: no pre-trained models found. "
                    "Ensemble will rely on momentum signals only."
                )

    # ------------------------------------------------------------------
    # Telegram control
    # ------------------------------------------------------------------

    def pause_trading(self):
        """Pause trading (called by Telegram /stop command)."""
        self.is_running = False
        self.logger.info("Trading paused via Telegram command")

    def resume_trading(self):
        """Resume trading (called by Telegram /resume command)."""
        self.is_running = True
        self.logger.info("Trading resumed via Telegram command")


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
