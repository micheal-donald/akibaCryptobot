"""Position lifecycle management — monitoring, exit checks, and closing trades."""

from datetime import datetime, timedelta
from typing import Dict


def timeframe_to_minutes(timeframe: str) -> int:
    """Convert a ccxt timeframe string (e.g. '1h', '15m', '1d') to minutes."""
    multipliers = {"m": 1, "h": 60, "d": 1440, "w": 10080}
    unit = timeframe[-1]
    value = int(timeframe[:-1])
    return value * multipliers.get(unit, 60)


class PositionManager:
    """Manages existing positions: trailing stops, exit signals, and closing trades."""

    def __init__(self, bot):
        self.bot = bot

    async def manage_positions(self):
        """Check each open position for trailing-stop updates and exit conditions."""
        try:
            # Snapshot keys — close_position() mutates active_trades mid-loop
            open_symbols = list(self.bot.active_trades.keys())
            for symbol in open_symbols:
                try:
                    # Get open orders for specific symbol
                    open_orders = await self.bot.exchange.get_open_orders(symbol)

                    for order in open_orders:
                        await self.check_order_conditions(order)

                    # Also check current position for exit conditions
                    ticker = await self.bot.data_exchange.get_ticker(symbol)
                    current_price = ticker["last"]

                    # Update trailing stop if enabled
                    if self.bot.risk_manager.trailing_stop:
                        new_trailing = self.bot.risk_manager.update_trailing_stop(
                            symbol, current_price
                        )
                        if new_trailing and symbol in self.bot.active_trades:
                            # Update the stop_loss in active_trades to the trailing value
                            trailing_stop_price = self.bot.risk_manager.positions.get(
                                symbol, {}
                            ).get("trailing_stop_price")
                            if trailing_stop_price:
                                self.bot.active_trades[symbol][
                                    "stop_loss"
                                ] = trailing_stop_price
                                # Persist to DB if trade_store exists
                                if hasattr(self.bot, "trade_store"):
                                    await self.bot.trade_store.update_trailing_stop(
                                        symbol, trailing_stop_price
                                    )

                    # Get OHLCV data for exit signal analysis
                    ohlcv_data = await self.bot.data_exchange.get_ohlcv(
                        symbol,
                        self.bot.config["trading"]["timeframes"]["primary"],
                        50,
                    )

                    # Check if we should exit the position
                    exit_signal = await self.bot.strategy_manager.get_active_strategy().should_exit_position(
                        symbol,
                        self.bot.active_trades[symbol],
                        current_price,
                        ohlcv_data,
                    )

                    if exit_signal:
                        await self.close_position(
                            symbol,
                            exit_signal.metadata.get("exit_reason", "strategy_exit"),
                        )

                except Exception as e:
                    self.bot.logger.error(f"Error managing position for {symbol}: {e}")

        except Exception as e:
            self.bot.logger.error(f"Error in manage_positions: {e}")

    async def check_order_conditions(self, order: Dict):
        """Check stop-loss and take-profit triggers for an open order."""
        symbol = order["symbol"]

        # Get current price
        ticker = await self.bot.data_exchange.get_ticker(symbol)
        current_price = ticker["last"]

        # Implement stop-loss and take-profit logic
        if symbol in self.bot.active_trades:
            trade = self.bot.active_trades[symbol]

            # Check stop-loss
            if self.should_stop_loss(trade, current_price):
                await self.close_position(symbol, "stop_loss")

            # Check take-profit
            elif self.should_take_profit(trade, current_price):
                await self.close_position(symbol, "take_profit")

    @staticmethod
    def should_stop_loss(trade: Dict, current_price: float) -> bool:
        if trade["side"] == "buy":
            return current_price <= trade["stop_loss"]
        else:
            return current_price >= trade["stop_loss"]

    @staticmethod
    def should_take_profit(trade: Dict, current_price: float) -> bool:
        if trade["side"] == "buy":
            return current_price >= trade["take_profit"]
        else:
            return current_price <= trade["take_profit"]

    async def close_position(self, symbol: str, reason: str):
        """Close an open position and record the result."""
        try:
            if symbol not in self.bot.active_trades:
                return

            trade = self.bot.active_trades[symbol]

            # Close the position
            side = "sell" if trade["side"] == "buy" else "buy"
            ticker = await self.bot.data_exchange.get_ticker(symbol)
            exit_price = ticker["last"]
            order = await self.bot.exchange.place_market_order(
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

            self.bot.logger.info(
                f"Closed {symbol}: gross PnL={gross_pnl:.4f}, "
                f"fees={total_fees:.4f}, net PnL={net_pnl:.4f}"
            )

            # Persist exit to database
            await self.bot.trade_store.update_trade_exit(
                symbol, exit_price, reason, net_pnl, total_fees
            )

            # Record in performance tracker
            await self.bot.performance_tracker.record_trade_exit(
                symbol=symbol,
                exit_price=exit_price,
                exit_reason=reason,
                fees=total_fees,
                trade_store=self.bot.trade_store,
            )

            if self.bot.telegram:
                await self.bot.telegram.send_message(
                    f"📊 Closed {symbol} position\n"
                    f"Reason: {reason}\n"
                    f"Exit Price: ${exit_price:.4f}\n"
                    f"Gross PnL: ${gross_pnl:.4f}\n"
                    f"Fees: ${total_fees:.4f}\n"
                    f"Net PnL: ${net_pnl:.4f}\n"
                    f"Order ID: {order['id']}"
                )

            # Unregister from risk manager
            closed_info = self.bot.risk_manager.remove_position(symbol, exit_price)
            if closed_info:
                self.bot.logger.info(
                    f"Trade PnL for {symbol}: {closed_info.get('pnl', 0):.6f}"
                )

            # Enforce cooldown after stop-loss exits to prevent immediate re-entry
            if reason in ("stop_loss", "trailing_stop"):
                cooldown_candles = self.bot.config.get("risk_management", {}).get(
                    "cooldown_candles", 5
                )
                timeframe = self.bot.config["trading"]["timeframes"]["primary"]
                candle_minutes = timeframe_to_minutes(timeframe)
                cooldown_delta = timedelta(minutes=candle_minutes * cooldown_candles)
                self.bot.pair_cooldowns[symbol] = datetime.now() + cooldown_delta
                self.bot.logger.info(
                    f"Cooldown set for {symbol}: {cooldown_candles} candles "
                    f"({cooldown_delta})"
                )

            # Remove from active trades
            del self.bot.active_trades[symbol]

        except Exception as e:
            self.bot.logger.error(f"Error closing position {symbol}: {e}")
