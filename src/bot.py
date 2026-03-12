import asyncio
import json
import os
from typing import Dict
from datetime import datetime, timedelta
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


class AkibaCryptoBot:
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

        # Performance tracking (powers /api/metrics and UI dashboard)
        initial_balance = self.config.get("trading", {}).get("initial_balance", 10000.0)
        self.performance_tracker = PerformanceMetricsTracker(
            initial_balance=initial_balance
        )

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
        self.pair_cooldowns: Dict[
            str, datetime
        ] = {}  # symbol -> cooldown-expiry datetime

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def load_config(self, config_path: str) -> Dict:
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
            return config
        except FileNotFoundError:
            self.logger.error(f"Config file not found: {config_path}")
            raise
        except json.JSONDecodeError:
            self.logger.error(f"Invalid JSON in config file: {config_path}")
            raise

    def signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.stop()

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
            while self.is_running:
                try:
                    await self.trading_cycle()
                    await asyncio.sleep(60)  # Run every minute
                except Exception as e:
                    self.logger.error(f"Error in trading cycle: {e}")
                    if self.telegram:
                        await self.telegram.send_message(
                            f"❌ Trading cycle error: {str(e)}"
                        )
                    await asyncio.sleep(60)

        except Exception as e:
            self.logger.error(f"Critical error: {e}")
            if self.telegram:
                await self.telegram.send_message(f"🚨 Critical error: {str(e)}")
        finally:
            await self.cleanup()

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
        await self.manage_positions()

        # Update correlations for risk management (every cycle)
        if len(self.price_data_cache) >= 2:
            self.advanced_risk.calculate_correlations(self.price_data_cache)

        # Look for new trading opportunities (only if circuit breaker is inactive)
        if not self.circuit_breaker_active:
            await self.scan_opportunities()
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
        """Update portfolio value history for circuit breaker tracking"""
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

            # Store portfolio value (keep last 1000 values)
            self.portfolio_value_history.append(total_value)
            if len(self.portfolio_value_history) > 1000:
                self.portfolio_value_history.pop(0)

        except Exception as e:
            self.logger.error(f"Error updating portfolio value: {e}")

    async def manage_positions(self):
        """Manage existing positions and orders"""
        try:
            # Check each active trade individually to avoid rate limits
            # Snapshot keys — close_position() mutates active_trades mid-loop
            open_symbols = list(self.active_trades.keys())
            for symbol in open_symbols:
                try:
                    # Get open orders for specific symbol
                    open_orders = await self.exchange.get_open_orders(symbol)

                    for order in open_orders:
                        await self.check_order_conditions(order)

                    # Also check current position for exit conditions
                    ticker = await self.data_exchange.get_ticker(symbol)
                    current_price = ticker["last"]

                    # Update trailing stop if enabled
                    if self.risk_manager.trailing_stop:
                        new_trailing = self.risk_manager.update_trailing_stop(
                            symbol, current_price
                        )
                        if new_trailing and symbol in self.active_trades:
                            # Update the stop_loss in active_trades to the trailing value
                            trailing_stop_price = self.risk_manager.positions.get(
                                symbol, {}
                            ).get("trailing_stop_price")
                            if trailing_stop_price:
                                self.active_trades[symbol][
                                    "stop_loss"
                                ] = trailing_stop_price
                                # Persist to DB if trade_store exists
                                if hasattr(self, "trade_store"):
                                    await self.trade_store.update_trailing_stop(
                                        symbol, trailing_stop_price
                                    )

                    # Get OHLCV data for exit signal analysis
                    ohlcv_data = await self.data_exchange.get_ohlcv(
                        symbol, self.config["trading"]["timeframes"]["primary"], 50
                    )

                    # Check if we should exit the position
                    exit_signal = await self.strategy_manager.get_active_strategy().should_exit_position(
                        symbol, self.active_trades[symbol], current_price, ohlcv_data
                    )

                    if exit_signal:
                        await self.close_position(
                            symbol,
                            exit_signal.metadata.get("exit_reason", "strategy_exit"),
                        )

                except Exception as e:
                    self.logger.error(f"Error managing position for {symbol}: {e}")

        except Exception as e:
            self.logger.error(f"Error in manage_positions: {e}")

    async def check_order_conditions(self, order: Dict):
        symbol = order["symbol"]

        # Get current price
        ticker = await self.data_exchange.get_ticker(symbol)
        current_price = ticker["last"]

        # Implement stop-loss and take-profit logic
        if symbol in self.active_trades:
            trade = self.active_trades[symbol]

            # Check stop-loss
            if self.should_stop_loss(trade, current_price):
                await self.close_position(symbol, "stop_loss")

            # Check take-profit
            elif self.should_take_profit(trade, current_price):
                await self.close_position(symbol, "take_profit")

    def should_stop_loss(self, trade: Dict, current_price: float) -> bool:
        if trade["side"] == "buy":
            return current_price <= trade["stop_loss"]
        else:
            return current_price >= trade["stop_loss"]

    def should_take_profit(self, trade: Dict, current_price: float) -> bool:
        if trade["side"] == "buy":
            return current_price >= trade["take_profit"]
        else:
            return current_price <= trade["take_profit"]

    async def close_position(self, symbol: str, reason: str):
        try:
            if symbol in self.active_trades:
                trade = self.active_trades[symbol]

                # Close the position
                side = "sell" if trade["side"] == "buy" else "buy"
                ticker = await self.data_exchange.get_ticker(symbol)
                exit_price = ticker["last"]
                order = await self.exchange.place_market_order(
                    symbol, side, trade["amount"], price=exit_price
                )

                # Extract exit fee and compute net PnL
                exit_fee_info = order.get("fee", {})
                exit_fee_cost = exit_fee_info.get("cost", 0.0) if exit_fee_info else 0.0

                entry_fee = trade.get("entry_fee", 0.0)
                total_fees = entry_fee + exit_fee_cost

                if trade["side"] == "buy":
                    gross_pnl = (exit_price - trade["entry_price"]) * trade["amount"]
                else:
                    gross_pnl = (trade["entry_price"] - exit_price) * trade["amount"]

                net_pnl = gross_pnl - total_fees

                self.logger.info(
                    f"Closed {symbol}: gross PnL={gross_pnl:.4f}, fees={total_fees:.4f}, net PnL={net_pnl:.4f}"
                )

                # Persist exit to database
                await self.trade_store.update_trade_exit(
                    symbol, exit_price, reason, net_pnl, total_fees
                )

                # Record in performance tracker
                self.performance_tracker.record_trade_exit(
                    symbol=symbol,
                    exit_price=exit_price,
                    exit_reason=reason,
                    fees=total_fees,
                )

                if self.telegram:
                    await self.telegram.send_message(
                        f"📊 Closed {symbol} position\n"
                        f"Reason: {reason}\n"
                        f"Exit Price: ${exit_price:.4f}\n"
                        f"Gross PnL: ${gross_pnl:.4f}\n"
                        f"Fees: ${total_fees:.4f}\n"
                        f"Net PnL: ${net_pnl:.4f}\n"
                        f"Order ID: {order['id']}"
                    )

                # Unregister from risk manager
                closed_info = self.risk_manager.remove_position(symbol, exit_price)
                if closed_info:
                    self.logger.info(
                        f"Trade PnL for {symbol}: {closed_info.get('pnl', 0):.6f}"
                    )

                # Enforce cooldown after stop-loss exits to prevent immediate re-entry
                if reason in ("stop_loss", "trailing_stop"):
                    cooldown_candles = self.config.get("risk_management", {}).get(
                        "cooldown_candles", 5
                    )
                    timeframe = self.config["trading"]["timeframes"]["primary"]
                    candle_minutes = self._timeframe_to_minutes(timeframe)
                    cooldown_delta = timedelta(
                        minutes=candle_minutes * cooldown_candles
                    )
                    self.pair_cooldowns[symbol] = datetime.now() + cooldown_delta
                    self.logger.info(
                        f"Cooldown set for {symbol}: {cooldown_candles} candles ({cooldown_delta})"
                    )

                # Remove from active trades
                del self.active_trades[symbol]

        except Exception as e:
            self.logger.error(f"Error closing position {symbol}: {e}")

    async def scan_opportunities(self):
        """Scan for trading opportunities using the active strategy"""
        try:
            for pair in self.config["trading"]["pairs"]:
                if pair in self.active_trades:
                    continue
                if self._at_max_trades():
                    break
                await self._process_pair(pair)
        except Exception as e:
            self.logger.error(f"Error in scan_opportunities: {e}")

    def _at_max_trades(self) -> bool:
        """Return True and log when the open-trade cap is reached."""
        max_trades = self.config["trading"].get("max_open_trades", 3)
        if len(self.active_trades) >= max_trades:
            self.logger.debug("Maximum open trades reached, skipping new opportunities")
            return True
        return False

    def _timeframe_to_minutes(self, timeframe: str) -> int:
        """Convert a ccxt timeframe string (e.g. '1h', '15m', '1d') to minutes."""
        multipliers = {"m": 1, "h": 60, "d": 1440, "w": 10080}
        unit = timeframe[-1]
        value = int(timeframe[:-1])
        return value * multipliers.get(unit, 60)

    async def _get_market_snapshot(self, pair: str):
        """Fetch OHLCV history and current price for a pair.

        Returns (ohlcv_data, current_price) or (None, None) on insufficient data.
        """
        ohlcv_data = await self.data_exchange.get_ohlcv(
            pair,
            self.config["trading"]["timeframes"]["primary"],
            200,
        )
        if ohlcv_data is None or len(ohlcv_data) < 50:
            self.logger.warning(f"Insufficient data for {pair}")
            return None, None
        ticker = await self.data_exchange.get_ticker(pair)
        return ohlcv_data, ticker["last"]

    async def _resolve_position_size(
        self, pair: str, signal, current_price: float
    ) -> float:
        """Return the position size in base currency, or 0 if balance is too low."""
        balance = self._cached_balance or await self.exchange.get_balance()
        usdt_balance = balance.get("USDT", {}).get("free", 0)

        if usdt_balance < 10:
            self.logger.warning(f"Insufficient balance: {usdt_balance} USDT")
            return 0

        use_atr = (
            self.config.get("advanced_risk", {}).get("use_atr_sizing", False)
            and pair in self.price_data_cache
        )
        if use_atr:
            sizing_result = self.advanced_risk.calculate_optimal_position_size(
                balance=usdt_balance,
                entry_price=current_price,
                symbol_data=self.price_data_cache[pair],
                symbol=pair,
                signal_strength=signal.strength,
            )
            self.logger.info(
                f"ATR-based position size for {pair}: {sizing_result.position_size:.6f} "
                f"(ATR: {sizing_result.atr:.2f}, Kelly: {sizing_result.kelly_fraction:.2f})"
            )
            return sizing_result.position_size

        return self.risk_manager.calculate_position_size_with_signal(
            usdt_balance, signal.strength, current_price, signal.stop_loss
        )

    async def _check_spread(self, symbol: str) -> bool:
        """Return True if bid-ask spread is acceptable, False if too wide.

        Fails open: if the order book is unavailable, the trade is allowed.
        """
        try:
            max_spread_pct = self.config.get("risk_management", {}).get(
                "max_spread_percent", 0.5
            )
            orderbook = await self.data_exchange.get_market_depth(symbol, limit=5)

            best_bid = orderbook["bids"][0][0] if orderbook.get("bids") else None
            best_ask = orderbook["asks"][0][0] if orderbook.get("asks") else None

            if best_bid is None or best_ask is None or best_bid <= 0:
                self.logger.warning(
                    f"No orderbook data for {symbol}, skipping spread check"
                )
                return True  # fail open

            spread_pct = ((best_ask - best_bid) / best_bid) * 100

            if spread_pct > max_spread_pct:
                self.logger.info(
                    f"Skipping {symbol}: spread {spread_pct:.3f}% > max {max_spread_pct}%"
                )
                return False

            return True
        except Exception as e:
            self.logger.error(f"Error checking spread for {symbol}: {e}")
            return True  # fail open — don't block trading on order book errors

    async def _process_pair(self, pair: str):
        """Evaluate a single pair and place a trade if conditions are met."""
        try:
            # Check cooldown — skip pairs that recently hit stop-loss
            if pair in self.pair_cooldowns:
                if datetime.now() < self.pair_cooldowns[pair]:
                    self.logger.debug(
                        f"Skipping {pair} - on cooldown until {self.pair_cooldowns[pair]}"
                    )
                    return
                else:
                    del self.pair_cooldowns[
                        pair
                    ]  # Cooldown expired, allow trading again

            ohlcv_data, current_price = await self._get_market_snapshot(pair)
            if ohlcv_data is None:
                return

            # Check bid-ask spread — skip illiquid pairs
            if not await self._check_spread(pair):
                return

            signal = await self.strategy_manager.generate_signal(
                pair, ohlcv_data, current_price
            )
            if not signal or not self.validate_trading_conditions(pair, signal):
                return

            if not self.advanced_risk.check_correlation_diversification(
                pair, self.active_trades
            ):
                self.logger.info(
                    f"Skipping {pair} - correlation diversification check failed"
                )
                return

            position_size = await self._resolve_position_size(
                pair, signal, current_price
            )
            if position_size > 0:
                await self.place_trade(
                    pair, signal.signal_type.value, position_size, current_price
                )
        except Exception as e:
            self.logger.error(f"Error processing {pair}: {e}")

    def validate_trading_conditions(self, symbol: str, signal) -> bool:
        """Validate if trading conditions are met"""
        try:
            # Check risk management limits
            if not self.risk_manager.can_open_position():
                self.logger.debug(f"Risk manager blocked new position for {symbol}")
                return False

            # Check if signal meets minimum strength
            if signal.strength < self.config.get("strategy_config", {}).get(
                "min_signal_strength", 0.45
            ):
                self.logger.debug(
                    f"Signal strength {signal.strength:.2f} below threshold for {symbol}"
                )
                return False

            return True

        except Exception as e:
            self.logger.error(f"Error validating trading conditions for {symbol}: {e}")
            return False

    async def place_trade(
        self, symbol: str, side: str, amount: float, entry_price: float
    ):
        try:
            # Calculate stop-loss and take-profit
            stop_loss = self.risk_manager.calculate_stop_loss(entry_price, side)
            take_profit = self.risk_manager.calculate_take_profit(entry_price, side)

            # Place the order
            order = await self.exchange.place_market_order(
                symbol, side, amount, price=entry_price
            )
            if order.get("status") == "rejected":
                self.logger.warning(
                    f"Order rejected for {symbol}: {order.get('reason')}"
                )
                return

            # Register position with risk manager so trailing stop and counters work
            self.risk_manager.add_position(
                symbol=symbol,
                side=side,
                amount=amount,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                order_id=order["id"],
            )

            # Extract entry fee from order response (ccxt provides this)
            fee_info = order.get("fee", {})
            entry_fee = fee_info.get("cost", 0.0) if fee_info else 0.0

            # Track the trade
            trade_dict = {
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "timestamp": datetime.now().isoformat(),
                "order_id": order["id"],
                "entry_fee": entry_fee,  # actual fee paid on entry
                "exit_fee": 0.0,  # filled on exit
            }
            db_id = await self.trade_store.insert_trade(trade_dict)
            trade_dict["db_id"] = db_id
            self.active_trades[symbol] = trade_dict

            # Record in performance tracker
            self.performance_tracker.record_trade_entry(
                symbol=symbol,
                side=side,
                amount=amount,
                entry_price=entry_price,
                strategy=self.strategy_manager.active_strategy or "unknown",
            )

            self.logger.info(f"Placed {side} order for {amount} {symbol}")

            if self.telegram:
                await self.telegram.send_message(
                    f"📈 New {side.upper()} trade\n"
                    f"Symbol: {symbol}\n"
                    f"Amount: {amount}\n"
                    f"Entry: ${entry_price:.4f}\n"
                    f"Stop Loss: ${stop_loss:.4f}\n"
                    f"Take Profit: ${take_profit:.4f}"
                )

        except Exception as e:
            self.logger.error(f"Error placing trade: {e}")

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

    # Methods for Telegram bot integration
    def get_status_info(self) -> Dict:
        """Get bot status information for Telegram commands"""
        uptime = datetime.now() - self.start_time
        return {
            "active_trades": len(self.active_trades),
            "daily_pnl": 0.0,  # Calculate actual PnL if needed
            "uptime": str(uptime),
            "is_running": self.is_running,
        }

    async def get_balance_info(self) -> Dict:
        """Get account balance for Telegram commands"""
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

    def get_active_trades(self) -> Dict:
        """Get active trades for Telegram commands"""
        return self.active_trades

    def get_config_info(self) -> Dict:
        """Get configuration info for Telegram commands"""
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

    def _load_strategies(self):
        """Load strategies based on configuration"""
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

    def pause_trading(self):
        """Pause trading (called by Telegram /stop command)"""
        self.is_running = False
        self.logger.info("Trading paused via Telegram command")

    def resume_trading(self):
        """Resume trading (called by Telegram /resume command)"""
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
