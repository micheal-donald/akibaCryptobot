"""
Performance metrics tracker for AkibaCryptoBot
Provides comprehensive performance analysis including Sharpe ratio, Sortino ratio,
max drawdown, profit factor, and other risk-adjusted metrics.
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
import pandas as pd
import numpy as np
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Represents a completed trade"""

    symbol: str
    side: str  # 'buy' or 'sell'
    entry_price: float
    exit_price: float
    amount: float
    entry_time: datetime
    exit_time: datetime
    pnl: float
    pnl_percent: float
    exit_reason: str
    strategy: str
    fees: float = 0.0
    metadata: Dict = field(default_factory=dict)

    @property
    def duration(self) -> timedelta:
        """Trade duration"""
        return self.exit_time - self.entry_time

    @property
    def is_winner(self) -> bool:
        """Check if trade was profitable"""
        return self.pnl > 0


class PerformanceMetricsTracker:
    """
    Tracks and calculates comprehensive performance metrics for trading bot
    """

    def __init__(self, initial_balance: float = 10000.0, risk_free_rate: float = 0.02):
        """
        Initialize performance tracker

        Args:
            initial_balance: Starting capital
            risk_free_rate: Annual risk-free rate for Sharpe/Sortino calculations (default 2%)
        """
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.risk_free_rate = risk_free_rate

        # Trade history
        self.trades: List[Trade] = []
        self.open_trades: Dict[str, Dict] = {}

        # Equity curve tracking
        self.equity_curve: List[Tuple[datetime, float]] = [
            (datetime.now(), initial_balance)
        ]
        self.daily_returns: List[float] = []
        self.daily_balance: Dict[datetime.date, float] = {}

        # Performance metrics cache
        self._metrics_cache: Optional[Dict] = None
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl = 60  # Cache valid for 60 seconds

        # Strategy-specific tracking
        self.strategy_performance: Dict[str, List[Trade]] = defaultdict(list)

        # Drawdown tracking
        self.peak_balance = initial_balance
        self.max_drawdown = 0.0
        self.max_drawdown_duration = timedelta(0)
        self.current_drawdown_start: Optional[datetime] = None

        logger.info(
            f"Performance tracker initialized with balance: ${initial_balance:.2f}"
        )

    def record_trade_entry(
        self,
        symbol: str,
        side: str,
        amount: float,
        entry_price: float,
        strategy: str = "unknown",
        metadata: Optional[Dict] = None,
    ):
        """
        Record a new trade entry

        Args:
            symbol: Trading pair
            side: 'buy' or 'sell'
            amount: Position size
            entry_price: Entry price
            strategy: Strategy name
            metadata: Additional trade metadata
        """
        self.open_trades[symbol] = {
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "entry_price": entry_price,
            "entry_time": datetime.now(),
            "strategy": strategy,
            "metadata": metadata or {},
        }

        logger.info(f"Recorded trade entry: {symbol} {side} {amount} @ {entry_price}")

    def record_trade_exit(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str = "unknown",
        fees: float = 0.0,
    ) -> Optional[Trade]:
        """
        Record trade exit and calculate PnL

        Args:
            symbol: Trading pair
            exit_price: Exit price
            exit_reason: Reason for exit (stop_loss, take_profit, strategy_exit, etc.)
            fees: Total trading fees

        Returns:
            Completed Trade object or None if no open trade found
        """
        if symbol not in self.open_trades:
            logger.warning(f"No open trade found for {symbol}")
            return None

        entry_trade = self.open_trades.pop(symbol)

        # Calculate PnL
        side = entry_trade["side"]
        amount = entry_trade["amount"]
        entry_price = entry_trade["entry_price"]

        if side == "buy":
            pnl = (exit_price - entry_price) * amount - fees
            pnl_percent = ((exit_price - entry_price) / entry_price) * 100
        else:  # sell
            pnl = (entry_price - exit_price) * amount - fees
            pnl_percent = ((entry_price - exit_price) / entry_price) * 100

        # Create Trade object
        trade = Trade(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            amount=amount,
            entry_time=entry_trade["entry_time"],
            exit_time=datetime.now(),
            pnl=pnl,
            pnl_percent=pnl_percent,
            exit_reason=exit_reason,
            strategy=entry_trade["strategy"],
            fees=fees,
            metadata=entry_trade["metadata"],
        )

        # Update balance
        self.current_balance += pnl
        self._update_equity_curve()

        # Track trade
        self.trades.append(trade)
        self.strategy_performance[trade.strategy].append(trade)

        # Update drawdown tracking
        self._update_drawdown()

        # Invalidate cache
        self._metrics_cache = None

        logger.info(
            f"Recorded trade exit: {symbol} PnL: ${pnl:.2f} ({pnl_percent:.2f}%) - {exit_reason}"
        )

        return trade

    def update_balance(self, new_balance: float):
        """
        Manually update current balance (for reconciliation)

        Args:
            new_balance: New balance value
        """
        old_balance = self.current_balance
        self.current_balance = new_balance
        self._update_equity_curve()
        self._update_drawdown()

        logger.info(f"Balance updated: ${old_balance:.2f} -> ${new_balance:.2f}")

    def _update_equity_curve(self):
        """Update equity curve with current balance"""
        now = datetime.now()
        self.equity_curve.append((now, self.current_balance))

        # Update daily balance
        today = now.date()
        self.daily_balance[today] = self.current_balance

        # Calculate daily return if we have previous day's balance
        yesterday = today - timedelta(days=1)
        if yesterday in self.daily_balance:
            yesterday_balance = self.daily_balance[yesterday]
            daily_return = (
                self.current_balance - yesterday_balance
            ) / yesterday_balance
            self.daily_returns.append(daily_return)

    def _update_drawdown(self):
        """Update drawdown metrics"""
        # Update peak
        if self.current_balance > self.peak_balance:
            self.peak_balance = self.current_balance
            # End of drawdown period
            if self.current_drawdown_start:
                drawdown_duration = datetime.now() - self.current_drawdown_start
                self.max_drawdown_duration = max(
                    self.max_drawdown_duration, drawdown_duration
                )
                self.current_drawdown_start = None
        else:
            # In drawdown
            if self.current_drawdown_start is None:
                self.current_drawdown_start = datetime.now()

            # Calculate current drawdown
            drawdown = (self.peak_balance - self.current_balance) / self.peak_balance
            self.max_drawdown = max(self.max_drawdown, drawdown)

    def calculate_sharpe_ratio(self, period: str = "daily") -> float:
        """
        Calculate Sharpe ratio

        Args:
            period: 'daily', 'weekly', or 'monthly'

        Returns:
            Sharpe ratio
        """
        if len(self.daily_returns) < 2:
            return 0.0

        returns = np.array(self.daily_returns)

        # Adjust risk-free rate to period
        if period == "daily":
            rf_rate = self.risk_free_rate / 252  # Trading days per year
        elif period == "weekly":
            rf_rate = self.risk_free_rate / 52
        else:  # monthly
            rf_rate = self.risk_free_rate / 12

        excess_returns = returns - rf_rate

        if np.std(returns) == 0:
            return 0.0

        sharpe = np.mean(excess_returns) / np.std(returns)

        # Annualize
        if period == "daily":
            sharpe *= np.sqrt(252)
        elif period == "weekly":
            sharpe *= np.sqrt(52)
        else:
            sharpe *= np.sqrt(12)

        return float(sharpe)

    def calculate_sortino_ratio(self, period: str = "daily") -> float:
        """
        Calculate Sortino ratio (focuses on downside deviation)

        Args:
            period: 'daily', 'weekly', or 'monthly'

        Returns:
            Sortino ratio
        """
        if len(self.daily_returns) < 2:
            return 0.0

        returns = np.array(self.daily_returns)

        # Adjust risk-free rate to period
        if period == "daily":
            rf_rate = self.risk_free_rate / 252
        elif period == "weekly":
            rf_rate = self.risk_free_rate / 52
        else:
            rf_rate = self.risk_free_rate / 12

        excess_returns = returns - rf_rate

        # Calculate downside deviation (only negative returns)
        downside_returns = returns[returns < 0]

        if len(downside_returns) == 0 or np.std(downside_returns) == 0:
            return 0.0

        downside_deviation = np.std(downside_returns)
        sortino = np.mean(excess_returns) / downside_deviation

        # Annualize
        if period == "daily":
            sortino *= np.sqrt(252)
        elif period == "weekly":
            sortino *= np.sqrt(52)
        else:
            sortino *= np.sqrt(12)

        return float(sortino)

    def calculate_profit_factor(self) -> float:
        """
        Calculate profit factor (gross profits / gross losses)

        Returns:
            Profit factor (>1 is profitable)
        """
        if not self.trades:
            return 0.0

        gross_profits = sum(trade.pnl for trade in self.trades if trade.pnl > 0)
        gross_losses = abs(sum(trade.pnl for trade in self.trades if trade.pnl < 0))

        if gross_losses == 0:
            return float("inf") if gross_profits > 0 else 0.0

        return gross_profits / gross_losses

    def calculate_win_rate(self) -> float:
        """
        Calculate win rate percentage

        Returns:
            Win rate as percentage (0-100)
        """
        if not self.trades:
            return 0.0

        winning_trades = sum(1 for trade in self.trades if trade.is_winner)
        return (winning_trades / len(self.trades)) * 100

    def calculate_expectancy(self) -> float:
        """
        Calculate average expected return per trade

        Returns:
            Expectancy (average PnL per trade)
        """
        if not self.trades:
            return 0.0

        return sum(trade.pnl for trade in self.trades) / len(self.trades)

    def calculate_average_win_loss_ratio(self) -> Tuple[float, float, float]:
        """
        Calculate average win, average loss, and win/loss ratio

        Returns:
            Tuple of (avg_win, avg_loss, win_loss_ratio)
        """
        if not self.trades:
            return 0.0, 0.0, 0.0

        winning_trades = [trade.pnl for trade in self.trades if trade.is_winner]
        losing_trades = [trade.pnl for trade in self.trades if not trade.is_winner]

        avg_win = np.mean(winning_trades) if winning_trades else 0.0
        avg_loss = abs(np.mean(losing_trades)) if losing_trades else 0.0

        win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

        return float(avg_win), float(avg_loss), float(win_loss_ratio)

    def get_metrics_summary(self, use_cache: bool = True) -> Dict:
        """
        Get comprehensive metrics summary

        Args:
            use_cache: Use cached metrics if available

        Returns:
            Dictionary with all performance metrics
        """
        # Check cache
        if use_cache and self._metrics_cache and self._cache_timestamp:
            if datetime.now() - self._cache_timestamp < timedelta(
                seconds=self._cache_ttl
            ):
                return self._metrics_cache

        # Calculate metrics
        total_return = (
            (self.current_balance - self.initial_balance) / self.initial_balance
        ) * 100
        total_pnl = self.current_balance - self.initial_balance

        avg_win, avg_loss, win_loss_ratio = self.calculate_average_win_loss_ratio()

        metrics = {
            # Balance metrics
            "initial_balance": self.initial_balance,
            "current_balance": self.current_balance,
            "total_pnl": total_pnl,
            "total_return_percent": total_return,
            # Trade statistics
            "total_trades": len(self.trades),
            "winning_trades": sum(1 for t in self.trades if t.is_winner),
            "losing_trades": sum(1 for t in self.trades if not t.is_winner),
            "win_rate": self.calculate_win_rate(),
            # Risk-adjusted metrics
            "sharpe_ratio": self.calculate_sharpe_ratio(),
            "sortino_ratio": self.calculate_sortino_ratio(),
            "profit_factor": self.calculate_profit_factor(),
            "expectancy": self.calculate_expectancy(),
            # Win/Loss analysis
            "average_win": avg_win,
            "average_loss": avg_loss,
            "win_loss_ratio": win_loss_ratio,
            # Drawdown metrics
            "max_drawdown_percent": self.max_drawdown * 100,
            "max_drawdown_amount": self.peak_balance * self.max_drawdown,
            "max_drawdown_duration_hours": self.max_drawdown_duration.total_seconds()
            / 3600,
            "current_drawdown_percent": (
                ((self.peak_balance - self.current_balance) / self.peak_balance) * 100
                if self.peak_balance > 0
                else 0
            ),
            # Open positions
            "open_trades_count": len(self.open_trades),
            # Timestamp
            "calculated_at": datetime.now().isoformat(),
        }

        # Cache results
        self._metrics_cache = metrics
        self._cache_timestamp = datetime.now()

        return metrics

    def get_strategy_performance(self, strategy_name: str) -> Dict:
        """
        Get performance metrics for a specific strategy

        Args:
            strategy_name: Name of the strategy

        Returns:
            Performance metrics for the strategy
        """
        strategy_trades = self.strategy_performance.get(strategy_name, [])

        if not strategy_trades:
            return {"strategy": strategy_name, "total_trades": 0}

        total_pnl = sum(t.pnl for t in strategy_trades)
        winning_trades = sum(1 for t in strategy_trades if t.is_winner)

        return {
            "strategy": strategy_name,
            "total_trades": len(strategy_trades),
            "winning_trades": winning_trades,
            "losing_trades": len(strategy_trades) - winning_trades,
            "win_rate": (winning_trades / len(strategy_trades)) * 100,
            "total_pnl": total_pnl,
            "average_pnl": total_pnl / len(strategy_trades),
        }

    def get_all_strategies_performance(self) -> Dict[str, Dict]:
        """
        Get performance metrics for all strategies

        Returns:
            Dictionary mapping strategy names to their performance metrics
        """
        return {
            strategy: self.get_strategy_performance(strategy)
            for strategy in self.strategy_performance.keys()
        }

    def get_recent_trades(self, limit: int = 10) -> List[Trade]:
        """
        Get most recent trades

        Args:
            limit: Number of trades to return

        Returns:
            List of recent Trade objects
        """
        return self.trades[-limit:] if self.trades else []

    def get_equity_curve_data(self) -> pd.DataFrame:
        """
        Get equity curve as DataFrame

        Returns:
            DataFrame with timestamp and balance columns
        """
        if not self.equity_curve:
            return pd.DataFrame(columns=["timestamp", "balance"])

        df = pd.DataFrame(self.equity_curve, columns=["timestamp", "balance"])
        return df

    def export_trades_to_dataframe(self) -> pd.DataFrame:
        """
        Export all trades to DataFrame for analysis

        Returns:
            DataFrame with all trade data
        """
        if not self.trades:
            return pd.DataFrame()

        trade_dicts = [
            {
                "symbol": t.symbol,
                "side": t.side,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "amount": t.amount,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "duration_hours": t.duration.total_seconds() / 3600,
                "pnl": t.pnl,
                "pnl_percent": t.pnl_percent,
                "exit_reason": t.exit_reason,
                "strategy": t.strategy,
                "fees": t.fees,
                "is_winner": t.is_winner,
            }
            for t in self.trades
        ]

        return pd.DataFrame(trade_dicts)

    def generate_performance_report(self) -> str:
        """
        Generate a formatted text performance report

        Returns:
            Formatted performance report string
        """
        metrics = self.get_metrics_summary()

        report = f"""
╔══════════════════════════════════════════════════════════════╗
║           AKIBA CRYPTO BOT - PERFORMANCE REPORT              ║
╚══════════════════════════════════════════════════════════════╝

📊 BALANCE & RETURNS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Initial Balance:      ${metrics["initial_balance"]:,.2f}
  Current Balance:      ${metrics["current_balance"]:,.2f}
  Total P&L:            ${metrics["total_pnl"]:,.2f}
  Total Return:         {metrics["total_return_percent"]:.2f}%

📈 TRADE STATISTICS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Total Trades:         {metrics["total_trades"]}
  Winning Trades:       {metrics["winning_trades"]}
  Losing Trades:        {metrics["losing_trades"]}
  Win Rate:             {metrics["win_rate"]:.2f}%

  Average Win:          ${metrics["average_win"]:.2f}
  Average Loss:         ${metrics["average_loss"]:.2f}
  Win/Loss Ratio:       {metrics["win_loss_ratio"]:.2f}

⚖️ RISK-ADJUSTED METRICS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Sharpe Ratio:         {metrics["sharpe_ratio"]:.3f}
  Sortino Ratio:        {metrics["sortino_ratio"]:.3f}
  Profit Factor:        {metrics["profit_factor"]:.3f}
  Expectancy:           ${metrics["expectancy"]:.2f}

📉 DRAWDOWN ANALYSIS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Max Drawdown:         {metrics["max_drawdown_percent"]:.2f}%
  Max DD Amount:        ${metrics["max_drawdown_amount"]:.2f}
  Max DD Duration:      {metrics["max_drawdown_duration_hours"]:.1f} hours
  Current Drawdown:     {metrics["current_drawdown_percent"]:.2f}%

📂 CURRENT STATUS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Open Positions:       {metrics["open_trades_count"]}

Generated: {metrics["calculated_at"]}
╚══════════════════════════════════════════════════════════════╝
"""
        return report.strip()

    def hydrate_from_db(self, closed_trades: List[Dict]):
        """
        Replay historical closed trades from the database into the tracker
        so that metrics survive bot restarts.

        Args:
            closed_trades: List of dicts from TradeStore.load_closed_trades()
        """
        for t in closed_trades:
            entry_price = t["entry_price"]
            exit_price = t["exit_price"]
            amount = t["amount"]
            side = t["side"]
            pnl = t.get("pnl", 0.0) or 0.0
            fees = t.get("fees", 0.0) or 0.0

            # Parse timestamps
            entry_time = datetime.fromisoformat(t["timestamp"])
            exit_time = (
                datetime.fromisoformat(t["exit_timestamp"])
                if t.get("exit_timestamp")
                else entry_time
            )

            # Compute pnl_percent
            if entry_price and entry_price > 0:
                if side == "buy":
                    pnl_percent = ((exit_price - entry_price) / entry_price) * 100
                else:
                    pnl_percent = ((entry_price - exit_price) / entry_price) * 100
            else:
                pnl_percent = 0.0

            trade = Trade(
                symbol=t["symbol"],
                side=side,
                entry_price=entry_price,
                exit_price=exit_price,
                amount=amount,
                entry_time=entry_time,
                exit_time=exit_time,
                pnl=pnl,
                pnl_percent=pnl_percent,
                exit_reason=t.get("exit_reason", "unknown"),
                strategy="ensemble",
                fees=fees,
            )

            self.trades.append(trade)
            self.strategy_performance[trade.strategy].append(trade)

            # Update balance and drawdown
            self.current_balance += pnl
            self.equity_curve.append((exit_time, self.current_balance))

            # Track daily balance for Sharpe/Sortino
            day = exit_time.date()
            prev_balance = self.daily_balance.get(day)
            self.daily_balance[day] = self.current_balance

            if prev_balance is None:
                # Check previous day
                yesterday = day - timedelta(days=1)
                if yesterday in self.daily_balance:
                    prev = self.daily_balance[yesterday]
                    self.daily_returns.append((self.current_balance - prev) / prev)

            self._update_drawdown()

        # Invalidate cache
        self._metrics_cache = None

        if closed_trades:
            logger.info(
                f"Hydrated {len(closed_trades)} historical trade(s): "
                f"balance=${self.current_balance:.2f}, "
                f"max_dd={self.max_drawdown * 100:.2f}%"
            )

    def reset(self):
        """Reset all metrics and trade history"""
        self.trades.clear()
        self.open_trades.clear()
        self.equity_curve = [(datetime.now(), self.initial_balance)]
        self.daily_returns.clear()
        self.daily_balance.clear()
        self.current_balance = self.initial_balance
        self.peak_balance = self.initial_balance
        self.max_drawdown = 0.0
        self.max_drawdown_duration = timedelta(0)
        self.current_drawdown_start = None
        self.strategy_performance.clear()
        self._metrics_cache = None

        logger.info("Performance tracker reset")
