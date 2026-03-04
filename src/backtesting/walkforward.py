import pandas as pd
import numpy as np
from typing import Dict, List, Tuple
from datetime import datetime, timedelta
import logging
from dataclasses import dataclass

from backtesting.engine import BacktestEngine, BacktestResult
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardResult:
    """Results from walk-forward analysis"""

    in_sample_results: List[BacktestResult]
    out_of_sample_results: List[BacktestResult]
    total_trades: int
    winning_trades: int
    total_pnl: float
    win_rate: float
    profit_factor: float
    max_drawdown: float
    sharpe_ratio: float
    avg_win: float
    avg_loss: float
    overfitting_score: (
        float  # Score indicating potential overfitting (0-1, higher = more overfitting)
    )
    equity_curve: List[float]
    timestamps: List[datetime]


class WalkForwardAnalyzer:
    """Walk-forward analyzer for backtesting strategies"""

    def __init__(self, config: Dict):
        self.config = config
        self.n_splits = config.get("walkforward", {}).get("n_splits", 5)
        self.train_period = config.get("walkforward", {}).get(
            "train_period", 180
        )  # days
        self.test_period = config.get("walkforward", {}).get("test_period", 60)  # days
        self.retrain_period = config.get("walkforward", {}).get(
            "retrain_period", 30
        )  # days

        logger.info(f"WalkForwardAnalyzer initialized with {self.n_splits} splits")
        logger.info(
            f"Train period: {self.train_period} days, Test period: {self.test_period} days"
        )

    async def run_walkforward_analysis(
        self, strategy: BaseStrategy, data: Dict[str, pd.DataFrame], symbols: List[str]
    ) -> WalkForwardResult:
        """
        Run walk-forward analysis on a strategy

        Args:
            strategy: Trading strategy to test
            data: Dictionary mapping symbols to OHLCV data
            symbols: List of symbols to test on

        Returns:
            WalkForwardResult with performance metrics
        """
        logger.info(f"Starting walk-forward analysis for {strategy.name}")

        # Combine data from all symbols
        combined_data = self._combine_data(data, symbols)
        if combined_data.empty:
            logger.warning("No data available for walk-forward analysis")
            return self._create_empty_result()

        # Sort by timestamp
        combined_data = combined_data.sort_index()

        # Create walk-forward periods
        periods = self._create_walkforward_periods(combined_data)
        if not periods:
            logger.warning("Could not create walk-forward periods")
            return self._create_empty_result()

        # Store results
        in_sample_results = []
        out_of_sample_results = []
        all_trades = []

        # Run walk-forward analysis
        for i, (train_start, train_end, test_start, test_end) in enumerate(periods):
            logger.info(f"Processing fold {i + 1}/{len(periods)}")
            logger.info(f"Train: {train_start} to {train_end}")
            logger.info(f"Test: {test_start} to {test_end}")

            # Create train and test datasets
            train_data = self._filter_data_by_time(data, train_start, train_end)
            test_data = self._filter_data_by_time(data, test_start, test_end)

            # Train strategy (for ML strategies)
            if hasattr(strategy, "train_model"):
                logger.info("Training model on in-sample data...")
                for symbol in symbols:
                    if symbol in train_data and not train_data[symbol].empty:
                        await strategy.train_model(symbol, train_data[symbol])

            # Run in-sample backtest
            in_sample_engine = BacktestEngine(self.config.get("backtesting", {}))
            in_sample_result = await in_sample_engine.run_backtest(
                strategy, train_data, symbols
            )
            in_sample_results.append(in_sample_result)

            # Run out-of-sample backtest
            out_of_sample_engine = BacktestEngine(self.config.get("backtesting", {}))
            out_of_sample_result = await out_of_sample_engine.run_backtest(
                strategy, test_data, symbols
            )
            out_of_sample_results.append(out_of_sample_result)

            # Collect trades
            all_trades.extend(out_of_sample_result.trades)

            # Retrain model periodically (for ML strategies)
            if hasattr(strategy, "train_model") and (i + 1) % self.retrain_period == 0:
                logger.info("Periodic retraining...")
                # Retrain on combined train + test data
                combined_train_test = self._combine_train_test_data(
                    train_data, test_data
                )
                for symbol in symbols:
                    if (
                        symbol in combined_train_test
                        and not combined_train_test[symbol].empty
                    ):
                        await strategy.train_model(symbol, combined_train_test[symbol])

        # Calculate overall metrics
        result = self._calculate_walkforward_metrics(
            in_sample_results, out_of_sample_results, all_trades
        )
        logger.info("Walk-forward analysis completed")

        return result

    def _combine_data(
        self, data: Dict[str, pd.DataFrame], symbols: List[str]
    ) -> pd.DataFrame:
        """Combine data from multiple symbols"""
        combined_rows = []

        for symbol in symbols:
            if symbol in data and not data[symbol].empty:
                symbol_data = data[symbol].copy()
                symbol_data["symbol"] = symbol
                combined_rows.append(symbol_data)

        if not combined_rows:
            return pd.DataFrame()

        # Concatenate all data
        combined_df = pd.concat(combined_rows)
        return combined_df

    def _create_walkforward_periods(
        self, combined_data: pd.DataFrame
    ) -> List[Tuple[datetime, datetime, datetime, datetime]]:
        """Create walk-forward periods"""
        if combined_data.empty:
            return []

        start_date = combined_data.index.min()
        end_date = combined_data.index.max()
        total_days = (end_date - start_date).days

        periods = []

        # Calculate step size
        step_days = total_days // self.n_splits

        for i in range(self.n_splits):
            # Training period
            train_start = start_date + timedelta(days=i * step_days)
            train_end = train_start + timedelta(days=self.train_period)

            # Testing period (starts right after training)
            test_start = train_end
            test_end = test_start + timedelta(days=self.test_period)

            # Ensure we don't go beyond available data
            if test_end > end_date:
                test_end = end_date

            periods.append((train_start, train_end, test_start, test_end))

        return periods

    def _filter_data_by_time(
        self, data: Dict[str, pd.DataFrame], start_time: datetime, end_time: datetime
    ) -> Dict[str, pd.DataFrame]:
        """Filter data by time period"""
        filtered_data = {}

        for symbol, df in data.items():
            if not df.empty:
                mask = (df.index >= start_time) & (df.index <= end_time)
                filtered_df = df[mask].copy()
                if not filtered_df.empty:
                    filtered_data[symbol] = filtered_df

        return filtered_data

    def _combine_train_test_data(
        self, train_data: Dict[str, pd.DataFrame], test_data: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.DataFrame]:
        """Combine train and test data for retraining"""
        combined_data = {}

        all_symbols = set(train_data.keys()) | set(test_data.keys())

        for symbol in all_symbols:
            dfs = []
            if symbol in train_data and not train_data[symbol].empty:
                dfs.append(train_data[symbol])
            if symbol in test_data and not test_data[symbol].empty:
                dfs.append(test_data[symbol])

            if dfs:
                combined_data[symbol] = pd.concat(dfs).sort_index()

        return combined_data

    def _calculate_walkforward_metrics(
        self,
        in_sample_results: List[BacktestResult],
        out_of_sample_results: List[BacktestResult],
        all_trades: List,
    ) -> WalkForwardResult:
        """Calculate walk-forward analysis metrics"""
        if not out_of_sample_results:
            return self._create_empty_result()

        # Calculate performance metrics from out-of-sample results
        total_trades = sum(r.total_trades for r in out_of_sample_results)
        winning_trades = sum(r.winning_trades for r in out_of_sample_results)
        total_pnl = sum(r.total_pnl for r in out_of_sample_results)

        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        # Calculate profit factor
        gross_profits = sum(
            sum(t.pnl for t in r.trades if t.pnl > 0) for r in out_of_sample_results
        )
        gross_losses = abs(
            sum(
                sum(t.pnl for t in r.trades if t.pnl < 0) for r in out_of_sample_results
            )
        )
        profit_factor = (
            gross_profits / gross_losses if gross_losses > 0 else float("inf")
        )

        # Calculate average win/loss
        wins = [t.pnl for r in out_of_sample_results for t in r.trades if t.pnl > 0]
        losses = [
            abs(t.pnl) for r in out_of_sample_results for t in r.trades if t.pnl < 0
        ]
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0

        # Calculate max drawdown
        equity_curves = [
            r.equity_curve for r in out_of_sample_results if r.equity_curve
        ]
        if equity_curves:
            combined_equity = []
            for curve in equity_curves:
                combined_equity.extend(curve)
            max_drawdown = self._calculate_max_drawdown(combined_equity)
        else:
            max_drawdown = 0.0

        # Calculate Sharpe ratio
        returns = [
            t.pnl_percentage
            for r in out_of_sample_results
            for t in r.trades
            if t.pnl_percentage is not None
        ]
        sharpe_ratio = self._calculate_sharpe_ratio(returns) if returns else 0

        # Calculate overfitting score
        overfitting_score = self._calculate_overfitting_score(
            in_sample_results, out_of_sample_results
        )

        # Combine equity curves and timestamps
        combined_equity_curve = []
        combined_timestamps = []
        for r in out_of_sample_results:
            combined_equity_curve.extend(r.equity_curve)
            combined_timestamps.extend(r.timestamps)

        return WalkForwardResult(
            in_sample_results=in_sample_results,
            out_of_sample_results=out_of_sample_results,
            total_trades=total_trades,
            winning_trades=winning_trades,
            total_pnl=total_pnl,
            win_rate=win_rate,
            profit_factor=profit_factor,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            avg_win=avg_win,
            avg_loss=avg_loss,
            overfitting_score=overfitting_score,
            equity_curve=combined_equity_curve,
            timestamps=combined_timestamps,
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

        # Annualize (assuming daily returns, 365 trading days)
        sharpe = (mean_excess / std_excess) * np.sqrt(365)
        return sharpe

    def _calculate_overfitting_score(
        self,
        in_sample_results: List[BacktestResult],
        out_of_sample_results: List[BacktestResult],
    ) -> float:
        """Calculate overfitting score based on performance degradation from in-sample to out-of-sample"""
        if not in_sample_results or not out_of_sample_results:
            return 0.0

        # Calculate average performance metrics
        avg_in_sample_win_rate = np.mean([r.win_rate for r in in_sample_results])
        avg_out_of_sample_win_rate = np.mean(
            [r.win_rate for r in out_of_sample_results]
        )

        # Calculate performance degradation (0-1, where 1 means complete degradation)
        if avg_in_sample_win_rate > 0:
            degradation = 1 - (avg_out_of_sample_win_rate / avg_in_sample_win_rate)
            overfitting_score = max(0, min(1, degradation))  # Clamp between 0 and 1
        else:
            overfitting_score = 0.0

        return overfitting_score

    def _create_empty_result(self) -> WalkForwardResult:
        """Create an empty walk-forward result"""
        return WalkForwardResult(
            in_sample_results=[],
            out_of_sample_results=[],
            total_trades=0,
            winning_trades=0,
            total_pnl=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            max_drawdown=0.0,
            sharpe_ratio=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            overfitting_score=0.0,
            equity_curve=[],
            timestamps=[],
        )

    def print_results(self, result: WalkForwardResult):
        """Print walk-forward analysis results in a formatted way"""
        print("\n" + "=" * 70)
        print("WALK-FORWARD ANALYSIS RESULTS")
        print("=" * 70)
        print(f"Total Trades: {result.total_trades}")
        print(f"Winning Trades: {result.winning_trades}")
        print(f"Win Rate: {result.win_rate:.2f}%")
        print(f"Total PnL: ${result.total_pnl:.6f}")
        print(f"Profit Factor: {result.profit_factor:.2f}")
        print(f"Max Drawdown: {result.max_drawdown:.2f}%")
        print(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")
        print(f"Avg Win: ${result.avg_win:.6f}")
        print(f"Avg Loss: ${result.avg_loss:.6f}")
        print(f"Overfitting Score: {result.overfitting_score:.2f} (0=none, 1=severe)")
        print("=" * 70)

        # Print fold-by-fold results
        print("\nFold-by-fold results:")
        for i, (in_sample, out_of_sample) in enumerate(
            zip(result.in_sample_results, result.out_of_sample_results)
        ):
            print(f"\nFold {i + 1}:")
            print(f"  In-sample Win Rate: {in_sample.win_rate:.2f}%")
            print(f"  Out-of-sample Win Rate: {out_of_sample.win_rate:.2f}%")
            print(
                f"  Performance Degradation: {in_sample.win_rate - out_of_sample.win_rate:.2f}%"
            )
