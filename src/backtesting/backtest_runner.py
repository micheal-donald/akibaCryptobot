#!/usr/bin/env python3
"""
Backtesting script for akibaCryptobot
"""

import asyncio
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os

# Add src to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from backtesting.engine import BacktestEngine
from backtesting.walkforward import WalkForwardAnalyzer
from strategies.momentum_strategy import MomentumStrategy
from strategies.freqai.ml_freqai_strategy import MLFreqAIStrategy
from bot_logging.logger import setup_logging

import logging

logger = logging.getLogger(__name__)


async def load_historical_data(
    symbols: list, timeframe: str = "1h", days: int = 365
) -> dict:
    """
    Load historical data for backtesting
    In a real implementation, this would load from CSV files or a database
    For now, we'll generate synthetic data for demonstration
    """
    print(f"Loading historical data for {len(symbols)} symbols...")

    data = {}

    # Generate synthetic data for each symbol
    for symbol in symbols:
        print(f"Generating data for {symbol}...")
        data[symbol] = generate_synthetic_data(symbol, timeframe, days)

    print("Data loading complete.")
    return data


def generate_synthetic_data(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """
    Generate synthetic OHLCV data for backtesting
    This is a simplified version - in practice you'd load real historical data
    """
    # Calculate number of periods
    if timeframe == "1h":
        periods = days * 24
    elif timeframe == "1d":
        periods = days
    else:
        periods = days * 24  # Default to hourly

    # Generate timestamps
    end_time = datetime.now()
    start_time = end_time - timedelta(days=days)
    timestamps = pd.date_range(start=start_time, periods=periods, freq="H")

    # Base parameters for synthetic data
    if "BTC" in symbol:
        base_price = 45000
        volatility = 0.03
    elif "ETH" in symbol:
        base_price = 3000
        volatility = 0.04
    else:
        base_price = 100
        volatility = 0.05

    # Generate price data using geometric Brownian motion
    prices = [base_price]
    for i in range(1, periods):
        # Random return with slight upward bias
        drift = 0.0001  # Small upward drift
        random_shock = np.random.normal(0, volatility)
        new_price = prices[-1] * np.exp(drift + random_shock)
        prices.append(new_price)

    # Create OHLCV data - ensure all arrays have the same length
    opens = prices[:-1]  # All but last
    closes = prices[1:]  # All but first
    highs = [
        max(o, c) * (1 + abs(np.random.normal(0, 0.01))) for o, c in zip(opens, closes)
    ]
    lows = [
        min(o, c) * (1 - abs(np.random.normal(0, 0.01))) for o, c in zip(opens, closes)
    ]
    volumes = [np.random.uniform(1000, 10000) for _ in range(len(opens))]

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=timestamps[:-1],
    )  # Match length with data

    return df


def load_config(config_path: str = "config/config.json"):
    """Load configuration from file"""
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Config file not found: {config_path}")
        return {}
    except json.JSONDecodeError as e:
        print(f"Error parsing config file: {e}")
        return {}


async def main():
    """Main backtesting function"""
    print("Starting akibaCryptobot Backtesting...")

    # Load configuration
    config = load_config()
    if not config:
        print("Failed to load configuration. Exiting.")
        return

    # Setup logging
    log_config = config.get("logging", {})
    logger = setup_logging(log_config)

    # Get symbols to test
    symbols = config.get("trading", {}).get("pairs", ["BTC/USDT"])
    print(f"Testing symbols: {symbols}")

    # Load historical data
    try:
        data = await load_historical_data(symbols, "1h", 30)  # 30 days of data
    except Exception as e:
        print(f"Error loading historical data: {e}")
        return

    # Check if we're running walk-forward analysis
    walkforward_enabled = (
        config.get("backtesting", {}).get("walkforward", {}).get("enabled", False)
    )

    if walkforward_enabled:
        # Initialize walk-forward analyzer
        print("Running walk-forward analysis...")
        walkforward_config = config.get("backtesting", {})
        analyzer = WalkForwardAnalyzer(walkforward_config)

        # Initialize strategy
        strategy_config = config.get("strategy_config", {})
        strategy = MLFreqAIStrategy(strategy_config)  # Use ML strategy for walk-forward

        try:
            result = await analyzer.run_walkforward_analysis(strategy, data, symbols)
            analyzer.print_results(result)
            save_walkforward_results_to_file(result, "walkforward_results.txt")
        except Exception as e:
            print(f"Error during walk-forward analysis: {e}")
            logger.error(f"Walk-forward analysis error: {e}", exc_info=True)
    else:
        # Initialize backtesting engine
        backtest_config = config.get(
            "backtesting",
            {"initial_balance": 10000, "fee": 0.001, "timerange": "20230101-20231231"},
        )

        engine = BacktestEngine(backtest_config)

        # Initialize strategy
        strategy_config = config.get("strategy_config", {})
        strategy = MomentumStrategy(strategy_config)

        # Run backtest
        print("Running backtest...")
        try:
            result = await engine.run_backtest(strategy, data, symbols)

            # Print results
            engine.print_results(result)

            # Additional analysis
            if result.trades:
                print("\nFirst 5 trades:")
                for i, trade in enumerate(result.trades[:5]):
                    print(
                        f"  {i + 1}. {trade.symbol} {trade.side.upper()} "
                        f"{trade.quantity:.6f} @ {trade.entry_price:.2f} -> "
                        f"{trade.exit_price:.2f} ({trade.pnl_percentage:.2f}%)"
                    )

            # Save results to file
            save_results_to_file(result, "backtest_results.txt")

        except Exception as e:
            print(f"Error during backtesting: {e}")
            logger.error(f"Backtesting error: {e}", exc_info=True)


def save_results_to_file(result, filename: str):
    """Save backtest results to a file"""
    try:
        with open(filename, "w") as f:
            f.write("BACKTEST RESULTS\n")
            f.write("=" * 50 + "\n")
            f.write(f"Total Trades: {result.total_trades}\n")
            f.write(f"Winning Trades: {result.winning_trades}\n")
            f.write(f"Losing Trades: {result.losing_trades}\n")
            f.write(f"Win Rate: {result.win_rate:.2f}%\n")
            f.write(
                f"Total PnL: ${result.total_pnl:.6f} ({result.total_pnl_percentage:.2f}%)\n"
            )
            f.write(f"Profit Factor: {result.profit_factor:.2f}\n")
            f.write(f"Max Drawdown: {result.max_drawdown:.2f}%\n")
            f.write(f"Sharpe Ratio: {result.sharpe_ratio:.2f}\n")
            f.write(f"Avg Win: ${result.avg_win:.6f}\n")
            f.write(f"Avg Loss: ${result.avg_loss:.6f}\n")
            f.write(f"Max Consecutive Wins: {result.max_consecutive_wins}\n")
            f.write(f"Max Consecutive Losses: {result.max_consecutive_losses}\n")

        print(f"Results saved to {filename}")
    except Exception as e:
        print(f"Error saving results to file: {e}")


def save_walkforward_results_to_file(result, filename: str):
    """Save walk-forward analysis results to a file"""
    try:
        with open(filename, "w") as f:
            f.write("WALK-FORWARD ANALYSIS RESULTS\n")
            f.write("=" * 50 + "\n")
            f.write(f"Total Trades: {result.total_trades}\n")
            f.write(f"Winning Trades: {result.winning_trades}\n")
            f.write(f"Win Rate: {result.win_rate:.2f}%\n")
            f.write(f"Total PnL: ${result.total_pnl:.6f}\n")
            f.write(f"Profit Factor: {result.profit_factor:.2f}\n")
            f.write(f"Max Drawdown: {result.max_drawdown:.2f}%\n")
            f.write(f"Sharpe Ratio: {result.sharpe_ratio:.2f}\n")
            f.write(f"Avg Win: ${result.avg_win:.6f}\n")
            f.write(f"Avg Loss: ${result.avg_loss:.6f}\n")
            f.write(
                f"Overfitting Score: {result.overfitting_score:.2f} (0=none, 1=severe)\n"
            )

            # Write fold-by-fold results
            f.write("\nFold-by-fold results:\n")
            for i, (in_sample, out_of_sample) in enumerate(
                zip(result.in_sample_results, result.out_of_sample_results)
            ):
                f.write(f"\nFold {i + 1}:\n")
                f.write(f"  In-sample Win Rate: {in_sample.win_rate:.2f}%\n")
                f.write(f"  Out-of-sample Win Rate: {out_of_sample.win_rate:.2f}%\n")
                f.write(
                    f"  Performance Degradation: {in_sample.win_rate - out_of_sample.win_rate:.2f}%\n"
                )

        print(f"Walk-forward results saved to {filename}")
    except Exception as e:
        print(f"Error saving walk-forward results to file: {e}")


if __name__ == "__main__":
    asyncio.run(main())
