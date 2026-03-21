import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import logging
from dataclasses import dataclass

from strategies.base_strategy import BaseStrategy, TradingSignal
from risk_management import RiskManager

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """Represents a trade in backtesting"""

    symbol: str
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime]
    exit_price: Optional[float]
    side: str  # 'buy' or 'sell'
    quantity: float
    entry_signal_strength: float
    pnl: Optional[float] = None
    pnl_percentage: Optional[float] = None
    exit_reason: Optional[str] = None
    fees: float = 0.0


@dataclass
class BacktestResult:
    """Results from a backtest"""

    total_trades: int
    winning_trades: int
    losing_trades: int
    total_pnl: float
    total_pnl_percentage: float
    win_rate: float
    profit_factor: float
    max_drawdown: float
    sharpe_ratio: float
    avg_win: float
    avg_loss: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    trades: List[BacktestTrade]
    equity_curve: List[float]
    timestamps: List[datetime]


class BacktestEngine:
    """Backtesting engine for cryptocurrency trading strategies"""

    def __init__(self, config: Dict):
        self.config = config
        self.initial_balance = config.get("initial_balance", 10000)
        self.fee_rate = config.get("fee", 0.001)  # 0.1% default fee
        self.timerange = config.get("timerange")

        # Risk manager for position sizing
        self.risk_manager = RiskManager(config.get("risk_management", {}))

        logger.info(
            f"BacktestEngine initialized with initial balance: ${self.initial_balance}"
        )

    async def run_backtest(
        self, strategy: BaseStrategy, data: Dict[str, pd.DataFrame], symbols: List[str]
    ) -> BacktestResult:
        """
        Run backtest for a strategy on historical data

        Args:
            strategy: Trading strategy to test
            data: Dictionary mapping symbols to OHLCV data
            symbols: List of symbols to test on

        Returns:
            BacktestResult with performance metrics
        """
        logger.info(f"Starting backtest for {strategy.name} on {len(symbols)} symbols")

        balance = self.initial_balance
        equity_curve = [balance]
        timestamps: List[datetime] = []
        all_trades: List[BacktestTrade] = []
        open_positions: Dict[str, BacktestTrade] = {}
        latest_prices: Dict[str, float] = {}

        combined_data = self._combine_and_sort_data(data, symbols)

        if combined_data.empty:
            logger.warning("No data available for backtesting")
            return self._create_empty_result()

        logger.info(f"Backtesting on {len(combined_data)} time periods")

        for _, row in combined_data.iterrows():
            timestamp = row.name
            symbol = row["symbol"]
            current_price = row["close"]
            latest_prices[symbol] = current_price
            ohlcv_data = data[symbol]
            current_data = ohlcv_data[ohlcv_data.index <= timestamp]

            if len(current_data) >= 50 and symbol in open_positions:
                closed_trade = await self._check_exit_conditions(
                    strategy,
                    open_positions,
                    symbol,
                    current_price,
                    current_data,
                    timestamp,
                )
                if closed_trade and closed_trade.pnl is not None:
                    balance += closed_trade.pnl

            if len(current_data) >= 50 and len(open_positions) < self.config.get(
                "max_open_trades", 3
            ):
                try:
                    signal = await strategy.generate_signal(
                        symbol, current_data, current_price
                    )

                    if signal and symbol not in open_positions:
                        if signal.strength >= strategy.min_signal_strength:
                            position_size = (
                                self.risk_manager.calculate_position_size_with_signal(
                                    balance,
                                    signal.strength,
                                    current_price,
                                    signal.stop_loss or (current_price * 0.98),
                                )
                            )

                            if position_size > 0:
                                trade = await self._execute_entry(
                                    symbol,
                                    signal,
                                    position_size,
                                    current_price,
                                    timestamp,
                                )
                                open_positions[symbol] = trade
                                all_trades.append(trade)

                except Exception as e:
                    logger.error(f"Error generating signal for {symbol}: {e}")

            equity_curve.append(
                self._calculate_current_equity(balance, open_positions, latest_prices)
            )
            timestamps.append(timestamp)

        for symbol, trade in list(open_positions.items()):
            final_price = data[symbol].iloc[-1]["close"]
            latest_timestamp = data[symbol].index[-1]
            latest_prices[symbol] = final_price
            await self._execute_exit(
                trade, final_price, "end_of_backtest", latest_timestamp
            )
            if trade.pnl is not None:
                balance += trade.pnl
            del open_positions[symbol]
            equity_curve.append(balance)
            timestamps.append(latest_timestamp)

        result = self._calculate_performance_metrics(
            all_trades, equity_curve, timestamps
        )
        logger.info(f"Backtest completed with {result.total_trades} trades")

        return result

    def _combine_and_sort_data(
        self, data: Dict[str, pd.DataFrame], symbols: List[str]
    ) -> pd.DataFrame:
        """Combine data from multiple symbols and sort by timestamp"""
        combined_rows = []

        for symbol in symbols:
            if symbol in data and not data[symbol].empty:
                symbol_data = data[symbol].copy()
                symbol_data["symbol"] = symbol
                combined_rows.append(symbol_data)

        if not combined_rows:
            return pd.DataFrame()

        combined_df = pd.concat(combined_rows)
        combined_df.sort_index(inplace=True)
        return combined_df

    async def _check_exit_conditions(
        self,
        strategy: BaseStrategy,
        open_positions: Dict[str, BacktestTrade],
        symbol: str,
        current_price: float,
        current_data: pd.DataFrame,
        timestamp: datetime,
    ) -> Optional[BacktestTrade]:
        """Check if we should exit an open position."""
        position = open_positions[symbol]

        exit_signal = await strategy.should_exit_position(
            symbol,
            {
                "side": position.side,
                "entry_price": position.entry_price,
                "amount": position.quantity,
            },
            current_price,
            current_data,
        )

        if exit_signal:
            await self._execute_exit(
                position,
                current_price,
                exit_signal.metadata.get("exit_reason", "strategy_exit"),
                timestamp,
            )
            del open_positions[symbol]
            return position

        max_hold_period = timedelta(days=self.config.get("max_hold_days", 7))
        if timestamp - position.entry_time > max_hold_period:
            await self._execute_exit(position, current_price, "time_exit", timestamp)
            del open_positions[symbol]
            return position

        return None

    async def _execute_entry(
        self,
        symbol: str,
        signal: TradingSignal,
        quantity: float,
        price: float,
        timestamp: datetime,
    ) -> BacktestTrade:
        """Execute an entry trade in backtesting"""
        fees = quantity * price * self.fee_rate

        trade = BacktestTrade(
            symbol=symbol,
            entry_time=timestamp,
            entry_price=price,
            exit_time=None,
            exit_price=None,
            side=signal.signal_type.value,
            quantity=quantity,
            entry_signal_strength=signal.strength,
            fees=fees,
        )

        logger.debug(
            f"Executed entry: {symbol} {signal.signal_type.value} {quantity:.6f} @ {price:.6f}"
        )
        return trade

    async def _execute_exit(
        self,
        trade: BacktestTrade,
        exit_price: float,
        exit_reason: str,
        timestamp: datetime,
    ):
        """Execute an exit trade in backtesting"""
        exit_fees = trade.quantity * exit_price * self.fee_rate
        total_fees = trade.fees + exit_fees

        if trade.side == "buy":
            pnl = (exit_price - trade.entry_price) * trade.quantity - total_fees
            pnl_percentage = (
                (exit_price - trade.entry_price) / trade.entry_price
            ) * 100
        else:
            pnl = (trade.entry_price - exit_price) * trade.quantity - total_fees
            pnl_percentage = (
                (trade.entry_price - exit_price) / trade.entry_price
            ) * 100

        trade.exit_time = timestamp
        trade.exit_price = exit_price
        trade.pnl = pnl
        trade.pnl_percentage = pnl_percentage
        trade.exit_reason = exit_reason
        trade.fees = total_fees

        logger.debug(
            f"Executed exit: {trade.symbol} {exit_reason} {trade.quantity:.6f} @ {exit_price:.6f} "
            f"PnL: {pnl:.6f} ({pnl_percentage:.2f}%)"
        )

    def _calculate_current_equity(
        self,
        balance: float,
        open_positions: Dict[str, BacktestTrade],
        latest_prices: Dict[str, float],
    ) -> float:
        """Approximate mark-to-market equity from open positions."""
        equity = balance
        for symbol, trade in open_positions.items():
            current_price = latest_prices.get(symbol, trade.entry_price)
            if trade.side == "buy":
                equity += (current_price - trade.entry_price) * trade.quantity
            else:
                equity += (trade.entry_price - current_price) * trade.quantity
            equity -= trade.fees
        return equity

    def _calculate_performance_metrics(
        self,
        trades: List[BacktestTrade],
        equity_curve: List[float],
        timestamps: List[datetime],
    ) -> BacktestResult:
        """Calculate performance metrics from completed trades"""
        if not trades:
            return self._create_empty_result()

        completed_trades = [t for t in trades if t.pnl is not None]

        if not completed_trades:
            return self._create_empty_result()

        total_trades = len(completed_trades)
        winning_trades = len([t for t in completed_trades if t.pnl > 0])
        losing_trades = total_trades - winning_trades

        total_pnl = sum(t.pnl for t in completed_trades)
        total_pnl_percentage = (total_pnl / self.initial_balance) * 100

        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        gross_profits = sum(t.pnl for t in completed_trades if t.pnl > 0)
        gross_losses = abs(sum(t.pnl for t in completed_trades if t.pnl < 0))
        profit_factor = (
            gross_profits / gross_losses if gross_losses > 0 else float("inf")
        )

        avg_win = (
            np.mean([t.pnl for t in completed_trades if t.pnl > 0])
            if winning_trades > 0
            else 0
        )
        avg_loss = (
            np.mean([abs(t.pnl) for t in completed_trades if t.pnl < 0])
            if losing_trades > 0
            else 0
        )

        max_drawdown = self._calculate_max_drawdown(equity_curve)
        returns = [
            t.pnl_percentage for t in completed_trades if t.pnl_percentage is not None
        ]
        sharpe_ratio = self._calculate_sharpe_ratio(returns) if returns else 0
        (
            max_consecutive_wins,
            max_consecutive_losses,
        ) = self._calculate_consecutive_trades(completed_trades)

        return BacktestResult(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            total_pnl=total_pnl,
            total_pnl_percentage=total_pnl_percentage,
            win_rate=win_rate,
            profit_factor=profit_factor,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            avg_win=avg_win,
            avg_loss=avg_loss,
            max_consecutive_wins=max_consecutive_wins,
            max_consecutive_losses=max_consecutive_losses,
            trades=completed_trades,
            equity_curve=equity_curve,
            timestamps=timestamps,
        )

    def _calculate_max_drawdown(self, equity_curve: List[float]) -> float:
        """Calculate maximum drawdown from equity curve"""
        if not equity_curve:
            return 0.0

        peak = equity_curve[0]
        max_dd = 0.0

        for equity in equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            if dd > max_dd:
                max_dd = dd

        return max_dd

    def _calculate_sharpe_ratio(
        self, returns: List[float], risk_free_rate: float = 0.0
    ) -> float:
        """Calculate Sharpe ratio"""
        if not returns:
            return 0.0

        excess_returns = [r - risk_free_rate for r in returns]
        mean_excess = np.mean(excess_returns)
        std_excess = np.std(excess_returns)

        if std_excess == 0:
            return 0.0

        sharpe = (mean_excess / std_excess) * np.sqrt(365)
        return sharpe

    def _calculate_consecutive_trades(
        self, trades: List[BacktestTrade]
    ) -> Tuple[int, int]:
        """Calculate max consecutive wins and losses"""
        if not trades:
            return 0, 0

        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0

        for trade in trades:
            if trade.pnl > 0:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            else:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)

        return max_wins, max_losses

    def _create_empty_result(self) -> BacktestResult:
        """Create an empty backtest result"""
        return BacktestResult(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            total_pnl=0.0,
            total_pnl_percentage=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            max_drawdown=0.0,
            sharpe_ratio=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            max_consecutive_wins=0,
            max_consecutive_losses=0,
            trades=[],
            equity_curve=[self.initial_balance],
            timestamps=[],
        )

    def print_results(self, result: BacktestResult):
        """Print backtest results in a formatted way"""
        print("\n" + "=" * 60)
        print("BACKTEST RESULTS")
        print("=" * 60)
        print(f"Total Trades: {result.total_trades}")
        print(f"Winning Trades: {result.winning_trades}")
        print(f"Losing Trades: {result.losing_trades}")
        print(f"Win Rate: {result.win_rate:.2f}%")
        print(
            f"Total PnL: ${result.total_pnl:.6f} ({result.total_pnl_percentage:.2f}%)"
        )
        print(f"Profit Factor: {result.profit_factor:.2f}")
        print(f"Max Drawdown: {result.max_drawdown:.2f}%")
        print(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")
        print(f"Avg Win: ${result.avg_win:.6f}")
        print(f"Avg Loss: ${result.avg_loss:.6f}")
        print(f"Max Consecutive Wins: {result.max_consecutive_wins}")
        print(f"Max Consecutive Losses: {result.max_consecutive_losses}")
        print("=" * 60)
