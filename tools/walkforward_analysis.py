#!/usr/bin/env python3
"""
Walk-Forward Analysis CLI Tool

This tool runs walk-forward analysis on trading strategies to validate
their performance and detect overfitting.

Usage:
    python tools/walkforward_analysis.py --strategy momentum --symbols BTC/USDT ETH/USDT
    python tools/walkforward_analysis.py --strategy freqai_ml_freqai --start 2024-01-01 --end 2024-12-31
    python tools/walkforward_analysis.py --config config/config.json --output results/wf_analysis.json
"""

import asyncio
import argparse
import json
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from backtesting.walkforward import WalkForwardAnalyzer
from backtesting.engine import BacktestEngine
from strategies.base_strategy import StrategyManager
from exchange.binance_client import BinanceClient
import pandas as pd
import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def load_config(config_path: str) -> dict:
    """Load configuration from file"""
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"Config file not found: {config_path}")
        sys.exit(1)
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in config file: {config_path}")
        sys.exit(1)


async def fetch_historical_data(
    exchange: BinanceClient,
    symbols: list,
    start_date: datetime,
    end_date: datetime,
    timeframe: str = "1h",
) -> dict:
    """Fetch historical OHLCV data for symbols"""
    logger.info(f"Fetching historical data for {len(symbols)} symbols...")
    logger.info(f"Date range: {start_date} to {end_date}")
    logger.info(f"Timeframe: {timeframe}")

    data = {}

    for symbol in symbols:
        try:
            logger.info(f"Fetching {symbol}...")

            # Calculate number of candles needed
            time_delta = end_date - start_date
            hours = int(time_delta.total_seconds() / 3600)

            if timeframe == "1h":
                limit = min(hours, 1000)
            elif timeframe == "4h":
                limit = min(hours // 4, 1000)
            elif timeframe == "1d":
                limit = min(hours // 24, 1000)
            else:
                limit = 1000

            # Fetch OHLCV data
            ohlcv = await exchange.get_ohlcv(symbol, timeframe, limit)

            if ohlcv is not None and not ohlcv.empty:
                # Filter by date range
                mask = (ohlcv.index >= start_date) & (ohlcv.index <= end_date)
                filtered_data = ohlcv[mask]

                if not filtered_data.empty:
                    data[symbol] = filtered_data
                    logger.info(f"  ✓ {symbol}: {len(filtered_data)} candles")
                else:
                    logger.warning(f"  ✗ {symbol}: No data in date range")
            else:
                logger.warning(f"  ✗ {symbol}: Failed to fetch data")

        except Exception as e:
            logger.error(f"  ✗ {symbol}: Error fetching data - {e}")

    logger.info(f"Successfully fetched data for {len(data)}/{len(symbols)} symbols")
    return data


async def run_walkforward_analysis(args):
    """Run walk-forward analysis"""
    print("=" * 70)
    print("WALK-FORWARD ANALYSIS TOOL")
    print("=" * 70)
    print()

    # Load configuration
    config = load_config(args.config)

    # Parse dates
    if args.start:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
    else:
        start_date = datetime.now() - timedelta(days=365)  # Default: 1 year ago

    if args.end:
        end_date = datetime.strptime(args.end, "%Y-%m-%d")
    else:
        end_date = datetime.now()

    # Get symbols
    symbols = args.symbols if args.symbols else config["trading"]["pairs"]

    # Initialize exchange client
    logger.info("Initializing exchange client...")
    exchange = BinanceClient(sandbox=config["exchange"].get("sandbox", True))

    # Fetch historical data
    data = await fetch_historical_data(
        exchange,
        symbols,
        start_date,
        end_date,
        timeframe=config["trading"]["timeframes"]["primary"],
    )

    if not data:
        logger.error("No data available for analysis. Exiting.")
        return

    # Initialize strategy
    logger.info(f"Initializing strategy: {args.strategy}")
    strategy_manager = StrategyManager(config)

    # Add and activate the requested strategy
    try:
        strategy_manager.add_strategy_by_name(args.strategy)
        strategy_manager.set_active_strategy(args.strategy)
        strategy = strategy_manager.get_active_strategy()
        logger.info(f"  ✓ Strategy loaded: {strategy.name}")
    except Exception as e:
        logger.error(f"Failed to load strategy '{args.strategy}': {e}")
        return

    # Configure walk-forward parameters
    wf_config = config.copy()
    if "walkforward" not in wf_config:
        wf_config["walkforward"] = {}

    if args.n_splits:
        wf_config["walkforward"]["n_splits"] = args.n_splits
    if args.train_period:
        wf_config["walkforward"]["train_period"] = args.train_period
    if args.test_period:
        wf_config["walkforward"]["test_period"] = args.test_period

    # Initialize walk-forward analyzer
    logger.info("Initializing walk-forward analyzer...")
    analyzer = WalkForwardAnalyzer(wf_config)

    # Run analysis
    logger.info("Starting walk-forward analysis...")
    print()
    result = await analyzer.run_walkforward_analysis(strategy, data, symbols)

    # Display results
    print()
    analyzer.print_results(result)

    # Save results if output path specified
    if args.output:
        output_data = {
            "timestamp": datetime.now().isoformat(),
            "strategy": args.strategy,
            "symbols": symbols,
            "date_range": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "results": {
                "total_trades": result.total_trades,
                "winning_trades": result.winning_trades,
                "win_rate": result.win_rate,
                "total_pnl": result.total_pnl,
                "profit_factor": result.profit_factor,
                "max_drawdown": result.max_drawdown,
                "sharpe_ratio": result.sharpe_ratio,
                "avg_win": result.avg_win,
                "avg_loss": result.avg_loss,
                "overfitting_score": result.overfitting_score,
            },
            "fold_results": [
                {
                    "fold": i + 1,
                    "in_sample_win_rate": in_sample.win_rate,
                    "out_of_sample_win_rate": out_of_sample.win_rate,
                    "degradation": in_sample.win_rate - out_of_sample.win_rate,
                }
                for i, (in_sample, out_of_sample) in enumerate(
                    zip(result.in_sample_results, result.out_of_sample_results)
                )
            ],
        }

        # Ensure output directory exists
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w") as f:
            json.dump(output_data, f, indent=2)

        logger.info(f"Results saved to {output_path}")

    # Interpretation
    print("\n" + "=" * 70)
    print("INTERPRETATION")
    print("=" * 70)

    if result.overfitting_score < 0.2:
        print("✅ LOW OVERFITTING RISK")
        print("   Strategy shows consistent performance in/out of sample.")
    elif result.overfitting_score < 0.4:
        print("⚠️  MODERATE OVERFITTING RISK")
        print("   Some performance degradation detected. Review strategy parameters.")
    else:
        print("❌ HIGH OVERFITTING RISK")
        print("   Significant performance degradation out-of-sample.")
        print("   Strategy may be overfit to historical data.")

    print()

    if result.sharpe_ratio > 1.5:
        print("✅ EXCELLENT RISK-ADJUSTED RETURNS (Sharpe > 1.5)")
    elif result.sharpe_ratio > 1.0:
        print("✅ GOOD RISK-ADJUSTED RETURNS (Sharpe > 1.0)")
    elif result.sharpe_ratio > 0.5:
        print("⚠️  MODERATE RISK-ADJUSTED RETURNS (Sharpe > 0.5)")
    else:
        print("❌ POOR RISK-ADJUSTED RETURNS (Sharpe < 0.5)")

    print()

    if result.max_drawdown < 10:
        print(f"✅ LOW DRAWDOWN ({result.max_drawdown:.2f}%)")
    elif result.max_drawdown < 20:
        print(f"⚠️  MODERATE DRAWDOWN ({result.max_drawdown:.2f}%)")
    else:
        print(f"❌ HIGH DRAWDOWN ({result.max_drawdown:.2f}%)")

    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Run walk-forward analysis on trading strategies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic analysis with default config
  python tools/walkforward_analysis.py --strategy momentum

  # Specify symbols and date range
  python tools/walkforward_analysis.py --strategy freqai_ml_freqai \\
      --symbols BTC/USDT ETH/USDT --start 2024-01-01 --end 2024-12-31

  # Custom walk-forward parameters
  python tools/walkforward_analysis.py --strategy momentum \\
      --n-splits 10 --train-period 120 --test-period 30

  # Save results to file
  python tools/walkforward_analysis.py --strategy momentum \\
      --output results/momentum_wf.json
        """,
    )

    parser.add_argument(
        "--strategy",
        type=str,
        required=True,
        help="Strategy name to analyze (e.g., momentum, freqai_ml_freqai)",
    )
    parser.add_argument(
        "--symbols", nargs="+", help="Trading symbols to analyze (default: from config)"
    )
    parser.add_argument(
        "--start", type=str, help="Start date (YYYY-MM-DD, default: 1 year ago)"
    )
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD, default: today)")
    parser.add_argument(
        "--config",
        type=str,
        default="config/config.json",
        help="Path to configuration file (default: config/config.json)",
    )
    parser.add_argument(
        "--n-splits",
        type=int,
        help="Number of walk-forward splits (default: from config)",
    )
    parser.add_argument(
        "--train-period",
        type=int,
        help="Training period in days (default: from config)",
    )
    parser.add_argument(
        "--test-period", type=int, help="Testing period in days (default: from config)"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file path for results JSON (default: no output)",
    )

    args = parser.parse_args()

    # Run analysis
    asyncio.run(run_walkforward_analysis(args))


if __name__ == "__main__":
    main()
