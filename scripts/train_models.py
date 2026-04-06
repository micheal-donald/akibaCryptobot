#!/usr/bin/env python3
"""
FreqAI Model Training Script

Downloads historical OHLCV data from Binance and trains LightGBM models
for each configured trading pair. Saves trained models as .joblib files
to the models/ directory.

Usage:
    python scripts/train_models.py
    python scripts/train_models.py --pairs BTC/USDT ETH/USDT
    python scripts/train_models.py --candles 2000 --config config/config.json
"""

import sys
import os
import argparse
import json
import logging
import asyncio

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from exchange.binance_client import BinanceClient
from strategies.freqai.ml_freqai_strategy import MLFreqAIStrategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("train_models")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Train FreqAI models for AkibaCryptoBot"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/config.json",
        help="Path to bot config (default: config/config.json)",
    )
    parser.add_argument(
        "--pairs",
        nargs="+",
        default=None,
        help="Override trading pairs (e.g., BTC/USDT ETH/USDT)",
    )
    parser.add_argument(
        "--candles",
        type=int,
        default=1000,
        help="Number of historical candles to fetch for training (default: 1000)",
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        default="1h",
        help="Candle timeframe (default: 1h)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="models",
        help="Directory to save trained models (default: models/)",
    )
    parser.add_argument(
        "--binance-us",
        action="store_true",
        default=False,
        help="Use binance.us API (for US-based CI runners)",
    )
    parser.add_argument(
        "--n-jobs",
        type=int,
        default=None,
        help="Override the ML model training parallelism (default: config value)",
    )
    return parser.parse_args()


async def train_all_models(
    config, pairs, candles, timeframe, output_dir, binance_us=False
):
    """Download data and train a model for each trading pair."""

    os.makedirs(output_dir, exist_ok=True)

    # Public OHLCV data — no API keys needed.
    # Use binance.us when running from US-based CI runners
    # (api.binance.com returns HTTP 451 for US IPs).
    exchange = BinanceClient(
        api_key="", secret_key="", sandbox=False, binance_us=binance_us
    )

    # Initialize the ML strategy
    strategy = MLFreqAIStrategy(config)

    results = {}

    for symbol in pairs:
        logger.info(f"\n{'='*60}")
        logger.info(f"Training model for {symbol}")
        logger.info(f"{'='*60}")

        try:
            # 1. Download historical data
            logger.info(f"Downloading {candles} x {timeframe} candles for {symbol}...")
            ohlcv_data = await exchange.get_ohlcv(symbol, timeframe, limit=candles)

            if ohlcv_data is None or len(ohlcv_data) < 200:
                logger.warning(
                    f"Insufficient data for {symbol}: got {len(ohlcv_data) if ohlcv_data is not None else 0} candles, need 200+"
                )
                results[symbol] = {"status": "skipped", "reason": "insufficient data"}
                continue

            logger.info(f"Downloaded {len(ohlcv_data)} candles for {symbol}")

            # Ensure 'date' column exists for time-based features
            if "date" not in ohlcv_data.columns:
                ohlcv_data["date"] = ohlcv_data.index

            # 2. Train the model
            await strategy.train_model(symbol, ohlcv_data)

            if not strategy.model_trained:
                logger.error(f"Model training failed for {symbol}")
                results[symbol] = {
                    "status": "failed",
                    "reason": "training returned no model",
                }
                continue

            # 3. Save the trained model
            safe_symbol = symbol.replace("/", "_")
            model_path = os.path.join(output_dir, f"{safe_symbol}_freqai.joblib")
            strategy.save_model(model_path)

            # 4. Log metrics
            logger.info(f"✅ Model saved for {symbol} → {model_path}")
            results[symbol] = {
                "status": "success",
                "model_path": model_path,
                "training_candles": len(ohlcv_data),
                "features": len(strategy.feature_columns),
            }

            # Reset for next symbol (fresh model per symbol)
            strategy.ml_model = None
            strategy.model_trained = False
            strategy.feature_columns = []

        except Exception as e:
            logger.error(f"❌ Failed to train model for {symbol}: {e}")
            results[symbol] = {"status": "error", "reason": str(e)}

    return results


def main():
    args = parse_args()

    # Load config
    logger.info(f"Loading config from {args.config}")
    with open(args.config, "r") as f:
        config = json.load(f)

    if args.n_jobs is not None:
        freqai_config = config.setdefault("freqai", {})
        ml_params = freqai_config.setdefault("ml_params", {})
        ml_params["n_jobs"] = int(args.n_jobs)

    # Determine pairs
    pairs = args.pairs or config.get("trading", {}).get("pairs", ["BTC/USDT"])
    logger.info(f"Training for pairs: {pairs}")
    logger.info(f"Candles: {args.candles}, Timeframe: {args.timeframe}")
    logger.info(f"Output directory: {args.output_dir}")
    if args.n_jobs is not None:
        logger.info(f"Training parallelism override: {args.n_jobs}")

    # Run training
    results = asyncio.run(
        train_all_models(
            config,
            pairs,
            args.candles,
            args.timeframe,
            args.output_dir,
            binance_us=args.binance_us,
        )
    )

    # Summary
    logger.info(f"\n{'='*60}")
    logger.info("TRAINING SUMMARY")
    logger.info(f"{'='*60}")
    for symbol, result in results.items():
        status = result["status"]
        icon = "✅" if status == "success" else "❌" if status == "error" else "⏭️"
        logger.info(f"  {icon} {symbol}: {status}")
        if status == "success":
            logger.info(
                f"     → {result['model_path']} ({result['features']} features, {result['training_candles']} candles)"
            )
        elif "reason" in result:
            logger.info(f"     → {result['reason']}")

    # Exit with error code if any failures
    failures = [s for s, r in results.items() if r["status"] == "error"]
    if failures:
        logger.error(f"\n{len(failures)} model(s) failed to train: {failures}")
        sys.exit(1)

    logger.info("\nAll models trained successfully!")


if __name__ == "__main__":
    main()
