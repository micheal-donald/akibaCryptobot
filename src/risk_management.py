from typing import Dict, Optional, Tuple
import logging
from datetime import datetime
import pandas as pd

# Import the new advanced risk management features
from advanced_risk_management import AdvancedRiskManager, PositionSizingResult

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, config: Dict):
        self.max_position_size = config.get(
            "max_position_size", 0.05
        )  # 5% of portfolio
        self.max_daily_loss = config.get("max_daily_loss", 0.02)  # 2% daily loss limit
        self.stop_loss_percent = config.get("stop_loss_percent", 0.02)  # 2% stop loss
        self.take_profit_percent = config.get(
            "take_profit_percent", 0.04
        )  # 4% take profit
        self.trailing_stop = config.get("trailing_stop", True)
        self.trailing_stop_positive = config.get("trailing_stop_positive", 0.01)  # 1%

        # Initialize advanced risk management
        self.advanced_risk = AdvancedRiskManager(config.get("advanced_risk", {}))

        # Risk tracking
        self.daily_pnl = 0.0
        self.daily_reset_time = None
        self.max_open_trades = config.get("max_open_trades", 3)
        self.current_open_trades = 0

        # Position tracking
        self.positions = {}
        self.daily_trades = 0
        self.max_daily_trades = config.get("max_daily_trades", 10)

        # Portfolio tracking
        self.portfolio_history = []

        logger.info("Risk Manager initialized with parameters:")
        logger.info(f"- Max position size: {self.max_position_size * 100}%")
        logger.info(f"- Max daily loss: {self.max_daily_loss * 100}%")
        logger.info(f"- Stop loss: {self.stop_loss_percent * 100}%")
        logger.info(f"- Take profit: {self.take_profit_percent * 100}%")

    def reset_daily_counters(self):
        """Reset daily counters at the start of each day"""
        now = datetime.now()
        if self.daily_reset_time is None or now.date() > self.daily_reset_time.date():
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self.daily_reset_time = now
            logger.info("Daily risk counters reset")

    def can_open_position(
        self, symbol: str = None, balance: float = None, risk_amount: float = None
    ) -> bool:
        """Check if a new position can be opened based on risk rules"""
        self.reset_daily_counters()

        # Check daily loss limit
        if balance and abs(self.daily_pnl) >= self.max_daily_loss * balance:
            return False

        # Check maximum open trades
        if self.current_open_trades >= self.max_open_trades:
            return False

        # Check daily trade limit
        if self.daily_trades >= self.max_daily_trades:
            return False

        # Check position size
        if balance and risk_amount and risk_amount > self.max_position_size * balance:
            return False

        # Check if already have position in this symbol
        if symbol and symbol in self.positions:
            return False

        return True

    def calculate_position_size(
        self, balance: float, entry_price: float, stop_loss_price: float, symbol: str
    ) -> float:
        """Calculate position size based on risk management rules"""
        try:
            # Calculate maximum risk amount
            max_risk_amount = balance * self.max_position_size

            # Calculate price difference for stop loss
            price_diff = abs(entry_price - stop_loss_price)
            if price_diff == 0:
                logger.warning(
                    "Stop loss price equals entry price, cannot calculate position size"
                )
                return 0.0

            # Calculate position size based on risk
            # Risk per unit = price difference
            # Position size = max_risk_amount / risk_per_unit
            position_size = max_risk_amount / price_diff

            # Apply additional safety margin
            position_size *= 0.95  # 5% safety margin

            logger.debug(f"Calculated position size for {symbol}: {position_size:.6f}")
            return position_size

        except Exception as e:
            logger.error(f"Error calculating position size: {e}")
            return 0.0

    def calculate_position_size_with_signal(
        self,
        balance: float,
        signal_strength: float,
        current_price: float,
        stop_loss_price: float,
    ) -> float:
        """Calculate position size with signal strength consideration"""
        try:
            # Base position size calculation
            base_size = self.calculate_position_size(
                balance, current_price, stop_loss_price, ""
            )

            # Adjust by signal strength
            adjusted_size = base_size * signal_strength

            # Ensure we don't exceed maximum position size
            max_position_value = balance * self.max_position_size
            max_quantity = max_position_value / current_price

            return min(adjusted_size, max_quantity)

        except Exception as e:
            logger.error(f"Error calculating position size with signal strength: {e}")
            return 0.0

    def calculate_advanced_position_size(
        self,
        balance: float,
        entry_price: float,
        symbol_data: pd.DataFrame,
        symbol: str,
        win_rate: float = 0.5,
        win_loss_ratio: float = 2.0,
    ) -> PositionSizingResult:
        """
        Calculate advanced position size using ATR and Kelly Criterion

        Args:
            balance: Current account balance
            entry_price: Current entry price
            symbol_data: Historical OHLCV data for the symbol
            symbol: Trading symbol
            win_rate: Strategy's historical win rate
            win_loss_ratio: Strategy's historical win/loss ratio

        Returns:
            PositionSizingResult with all calculations
        """
        return self.advanced_risk.calculate_position_sizing(
            balance, entry_price, symbol_data, symbol, win_rate, win_loss_ratio
        )

    def calculate_stop_loss(self, entry_price: float, side: str) -> float:
        """Calculate stop loss price"""
        if side.lower() == "buy":
            stop_loss = entry_price * (1 - self.stop_loss_percent)
        else:  # sell
            stop_loss = entry_price * (1 + self.stop_loss_percent)

        return stop_loss

    def calculate_take_profit(self, entry_price: float, side: str) -> float:
        """Calculate take profit price"""
        if side.lower() == "buy":
            take_profit = entry_price * (1 + self.take_profit_percent)
        else:  # sell
            take_profit = entry_price * (1 - self.take_profit_percent)

        return take_profit

    def update_trailing_stop(
        self, symbol: str, current_price: float
    ) -> Optional[float]:
        """Update trailing stop loss if applicable."""
        if not self.trailing_stop or symbol not in self.positions:
            return None

        position = self.positions[symbol]
        if "trailing_stop_price" not in position:
            return None

        side = position["side"].lower()
        entry_price = position["entry_price"]
        current_trailing_stop = position.get("trailing_stop_price")

        if side == "buy":
            return self._update_long_trailing_stop(
                symbol, position, current_price, entry_price, current_trailing_stop
            )
        return self._update_short_trailing_stop(
            symbol, position, current_price, entry_price, current_trailing_stop
        )

    def _update_long_trailing_stop(
        self,
        symbol: str,
        position: Dict,
        current_price: float,
        entry_price: float,
        current_trailing_stop: Optional[float],
    ) -> Optional[float]:
        """Ratchet trailing stop upward for long positions."""
        if current_price <= entry_price * (1 + self.trailing_stop_positive):
            return current_trailing_stop
        new_trailing_stop = current_price * (1 - self.stop_loss_percent)
        if current_trailing_stop is None or new_trailing_stop > current_trailing_stop:
            position["trailing_stop_price"] = new_trailing_stop
            logger.info(f"Updated trailing stop for {symbol}: {new_trailing_stop:.6f}")
            return new_trailing_stop
        return current_trailing_stop

    def _update_short_trailing_stop(
        self,
        symbol: str,
        position: Dict,
        current_price: float,
        entry_price: float,
        current_trailing_stop: Optional[float],
    ) -> Optional[float]:
        """Ratchet trailing stop downward for short positions."""
        if current_price >= entry_price * (1 - self.trailing_stop_positive):
            return current_trailing_stop
        new_trailing_stop = current_price * (1 + self.stop_loss_percent)
        if current_trailing_stop is None or new_trailing_stop < current_trailing_stop:
            position["trailing_stop_price"] = new_trailing_stop
            logger.info(f"Updated trailing stop for {symbol}: {new_trailing_stop:.6f}")
            return new_trailing_stop
        return current_trailing_stop

    def _check_exit_trigger(
        self,
        is_long: bool,
        price: float,
        stop_loss: Optional[float],
        trailing_stop: Optional[float],
        take_profit: Optional[float],
    ) -> Tuple[bool, str]:
        """Check individual exit triggers for a position."""
        if stop_loss and (
            (is_long and price <= stop_loss) or (not is_long and price >= stop_loss)
        ):
            return True, "stop_loss"
        if trailing_stop and (
            (is_long and price <= trailing_stop)
            or (not is_long and price >= trailing_stop)
        ):
            return True, "trailing_stop"
        if take_profit and (
            (is_long and price >= take_profit) or (not is_long and price <= take_profit)
        ):
            return True, "take_profit"
        return False, "hold"

    def should_close_position(
        self, symbol: str, current_price: float
    ) -> Tuple[bool, str]:
        """Check if position should be closed based on risk rules."""
        if symbol not in self.positions:
            return False, "No position found"

        position = self.positions[symbol]
        is_long = position["side"].lower() == "buy"

        return self._check_exit_trigger(
            is_long,
            current_price,
            position.get("stop_loss"),
            position.get("trailing_stop_price"),
            position.get("take_profit"),
        )

    def add_position(
        self,
        symbol: str,
        side: str,
        amount: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        order_id: str,
    ):
        """Add a new position to tracking"""
        self.positions[symbol] = {
            "side": side,
            "amount": amount,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "order_id": order_id,
            "timestamp": datetime.now(),
            "trailing_stop_price": None if not self.trailing_stop else stop_loss,
        }

        self.current_open_trades += 1
        self.daily_trades += 1

        logger.info(f"Added position: {symbol} {side} {amount} @ {entry_price}")

    def remove_position(self, symbol: str, exit_price: float) -> Optional[Dict]:
        """Remove position and calculate PnL"""
        if symbol not in self.positions:
            return None

        position = self.positions[symbol]

        # Calculate PnL
        entry_price = position["entry_price"]
        amount = position["amount"]
        side = position["side"]

        if side.lower() == "buy":
            pnl = (exit_price - entry_price) * amount
        else:
            pnl = (entry_price - exit_price) * amount

        # Update daily PnL
        self.daily_pnl += pnl

        # Remove position
        closed_position = self.positions.pop(symbol)
        closed_position["exit_price"] = exit_price
        closed_position["pnl"] = pnl
        closed_position["exit_timestamp"] = datetime.now()

        self.current_open_trades -= 1

        logger.info(f"Closed position: {symbol} PnL: {pnl:.6f}")

        return closed_position

    def get_portfolio_risk(self, balance: float) -> Dict:
        """Get current portfolio risk metrics"""
        total_exposure = sum(
            pos["amount"] * pos["entry_price"] for pos in self.positions.values()
        )

        exposure_ratio = total_exposure / balance if balance > 0 else 0

        return {
            "total_exposure": total_exposure,
            "exposure_ratio": exposure_ratio,
            "open_positions": self.current_open_trades,
            "daily_pnl": self.daily_pnl,
            "daily_trades": self.daily_trades,
            "daily_pnl_percent": self.daily_pnl / balance if balance > 0 else 0,
        }

    def check_correlation_diversification(self, symbol: str) -> bool:
        """
        Check if adding a new position would violate correlation diversification rules.

        Args:
            symbol: Symbol to check

        Returns:
            True if position can be added, False otherwise
        """
        return self.advanced_risk.check_correlation_diversification(
            symbol, self.positions
        )

    def check_circuit_breakers(
        self, price_data: Dict[str, pd.DataFrame]
    ) -> Tuple[bool, str]:
        """
        Check circuit breakers for extreme market conditions

        Args:
            price_data: Dictionary mapping symbols to OHLCV data

        Returns:
            Tuple of (should_stop_trading, reason)
        """
        return self.advanced_risk.check_circuit_breakers(
            price_data, self.portfolio_history
        )

    def update_portfolio_value(self, portfolio_value: float):
        """
        Update portfolio value history for drawdown calculations

        Args:
            portfolio_value: Current portfolio value
        """
        self.portfolio_history.append(portfolio_value)
        # Keep only the last 1000 values to prevent memory issues
        if len(self.portfolio_history) > 1000:
            self.portfolio_history = self.portfolio_history[-1000:]

    def generate_risk_report(self) -> Dict:
        """
        Generate a comprehensive risk report

        Returns:
            Dictionary with risk metrics
        """
        return self.advanced_risk.get_risk_report()

    def emergency_close_all_positions(self) -> bool:
        """Emergency function to mark all positions for closure"""
        logger.warning("EMERGENCY: Marking all positions for immediate closure")

        # This would typically trigger immediate market orders to close all positions
        # Implementation depends on exchange integration

        emergency_positions = list(self.positions.keys())
        logger.critical(
            f"Emergency closure triggered for positions: {emergency_positions}"
        )

        return True
