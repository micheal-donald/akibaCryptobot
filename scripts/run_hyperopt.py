#!/usr/bin/env python3
"""Run Optuna-based hyperparameter optimization for AkibaCryptoBot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from backtesting.data_loader import load_historical_data
from optimization.hyperopt import StrategyHyperoptimizer, available_strategies


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AkibaCryptoBot hyperopt runner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", default="config/config.json", help="Path to config")
    parser.add_argument(
        "--strategy",
        default=None,
        help=f"Strategy to optimize ({', '.join(available_strategies())})",
    )
    parser.add_argument("-e", "--epochs", type=int, default=50, help="Number of trials")
    parser.add_argument(
        "--spaces",
        nargs="+",
        default=["default"],
        help="Spaces to optimize: default, buy, sell, roi, stoploss, trailing, trades, all",
    )
    parser.add_argument(
        "--loss",
        default="ProfitDrawDownHyperOptLoss",
        help="Hyperopt loss function name",
    )
    parser.add_argument("--pairs", nargs="+", default=None, help="Pairs to optimize")
    parser.add_argument("--timeframe", default=None, help="Primary timeframe override")
    parser.add_argument("--timerange", default=None, help="YYYYMMDD-YYYYMMDD")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Synthetic fallback window; overrides config timerange when set",
    )
    parser.add_argument(
        "--data-dir", default=None, help="Directory containing OHLCV files"
    )
    parser.add_argument("--jobs", type=int, default=1, help="Concurrent Optuna workers")
    parser.add_argument("--random-state", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--min-trades", type=int, default=5, help="Minimum trades per trial"
    )
    parser.add_argument(
        "--early-stop", type=int, default=0, help="Stop after N stale trials"
    )
    parser.add_argument(
        "--top-n", type=int, default=5, help="Number of top trials to keep"
    )
    parser.add_argument(
        "--no-synthetic-fallback",
        action="store_true",
        help="Require external OHLCV files instead of generating fallback data",
    )
    parser.add_argument("--print-json", action="store_true", help="Print JSON summary")
    parser.add_argument(
        "--export-summary",
        default=None,
        help="Where to write the hyperopt summary JSON",
    )
    parser.add_argument(
        "--export-config",
        default=None,
        help="Where to write the merged best config JSON",
    )
    return parser.parse_args()


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def print_summary(summary) -> None:
    print("\n=== Hyperopt Summary ===")
    print(f"Strategy: {summary.strategy}")
    print(f"Loss: {summary.loss_function}")
    print(f"Spaces: {', '.join(summary.spaces)}")
    print(f"Best Trial: #{summary.best.number}")
    print(f"Objective: {summary.best.loss:.6f}")
    print(f"Trades: {summary.best.total_trades}")
    print(
        f"PnL: {summary.best.total_pnl:.4f} ({summary.best.total_pnl_percentage:.2f}%)"
    )
    print(f"Win Rate: {summary.best.win_rate:.2f}%")
    print(f"Profit Factor: {summary.best.profit_factor:.2f}")
    print(f"Max Drawdown: {summary.best.max_drawdown:.2f}%")
    print(f"Sharpe Ratio: {summary.best.sharpe_ratio:.2f}")
    print("Best Params:")
    for key, value in summary.best.params.items():
        print(f"  {key}: {value}")


def main() -> int:
    args = parse_args()
    config = load_config(args.config)

    strategy_name = args.strategy or config.get("trading", {}).get(
        "strategy", "momentum"
    )
    config.setdefault("trading", {})
    config.setdefault("backtesting", {})
    config.setdefault("risk_management", {})
    config.setdefault("strategy_config", {})

    if args.pairs:
        config["trading"]["pairs"] = args.pairs
    pairs = config["trading"].get("pairs", ["BTC/USDT"])

    if args.timeframe:
        config["trading"].setdefault("timeframes", {})["primary"] = args.timeframe
    timeframe = config["trading"].get("timeframes", {}).get("primary", "1h")

    timerange = args.timerange
    if timerange is None and args.days is None:
        timerange = config.get("backtesting", {}).get("timerange")
    if timerange:
        config["backtesting"]["timerange"] = timerange

    days = args.days if args.days is not None else 180

    data = load_historical_data(
        symbols=pairs,
        timeframe=timeframe,
        days=days,
        data_dir=args.data_dir,
        timerange=timerange,
        synthetic_fallback=not args.no_synthetic_fallback,
        random_seed=args.random_state,
    )
    if not data:
        print("No historical OHLCV data found for the selected pairs.", file=sys.stderr)
        return 1

    optimizer = StrategyHyperoptimizer(
        config=config,
        strategy_name=strategy_name,
        loss_name=args.loss,
        spaces=args.spaces,
        min_trades=args.min_trades,
        random_state=args.random_state,
    )

    try:
        summary = optimizer.optimize(
            data=data,
            epochs=args.epochs,
            jobs=args.jobs,
            early_stop=args.early_stop,
            top_n=args.top_n,
        )
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    results_dir = Path("results") / "hyperopt"
    summary_path = args.export_summary or str(
        results_dir / f"{strategy_name}_summary.json"
    )
    config_path = args.export_config or str(
        results_dir / f"{strategy_name}_best_config.json"
    )

    summary.export_json(summary_path)
    optimizer.export_merged_config(summary.best.params, config_path)

    if args.print_json:
        print(json.dumps(summary.to_dict(), indent=2))
    else:
        print_summary(summary)
        print(f"Summary written to: {summary_path}")
        print(f"Best config written to: {config_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
