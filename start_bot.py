#!/usr/bin/env python3
"""
Start script for AkibaCryptoBot with FastAPI + Trading Engine

Launches both the HTTP/WebSocket API server and the trading bot
in a single asyncio event loop via uvicorn.
"""

import sys
import os
import argparse
import logging
import uvicorn

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def setup_logging(debug=False):
    """Setup basic logging configuration"""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("logs/akiba_crypto_bot_startup.log"),
        ],
    )
    return logging.getLogger(__name__)


def log_runtime_visibility(logger, bot):
    """Log the selected deployment profile and effective runtime ownership."""

    visibility = bot.get_runtime_visibility_info()
    logger.info(
        "Deployment profile=%s effective_runtime_mode=%s "
        "effective_runtime_enabled=%s effective_runtime_owns_entries=%s "
        "effective_runtime_owns_exits=%s runtime_mode_source=%s",
        visibility["configured_deployment_profile"],
        visibility["effective_runtime_mode"],
        visibility["effective_runtime_enabled"],
        visibility["effective_runtime_owns_entries"],
        visibility["effective_runtime_owns_exits"],
        visibility["runtime_mode_source"],
    )


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="AkibaCryptoBot - HFT Engine with FastAPI Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  start_bot.py                              # Start with default config
  start_bot.py --strategy ensemble          # Start with Ensemble (ML+TA) strategy
  start_bot.py --testnet --debug            # Start in testnet mode with debug logging
  start_bot.py --port 8080                  # Custom API port
  start_bot.py --no-api                     # Run bot only, no API server
        """,
    )

    parser.add_argument(
        "--config",
        type=str,
        default="config/config.json",
        help="Path to configuration file (default: config/config.json)",
    )

    parser.add_argument(
        "--strategy",
        type=str,
        help="Override strategy from config file (e.g., momentum, ensemble, freqai_ml_freqai)",
    )

    parser.add_argument(
        "--testnet", action="store_true", help="Run in testnet mode (paper trading)"
    )

    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    parser.add_argument(
        "--dry-run", action="store_true", help="Run in dry-run mode (no actual trades)"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the FastAPI server (default: 8000)",
    )

    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind the FastAPI server (default: 0.0.0.0)",
    )

    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Run the trading bot without the API server (legacy mode)",
    )

    return parser.parse_args()


def main():
    """Main entry point"""
    args = parse_arguments()
    logger = setup_logging(args.debug)

    logger.info("=" * 50)
    logger.info("Starting AkibaCryptoBot")
    logger.info("=" * 50)
    logger.info(f"Configuration file: {args.config}")
    logger.info(f"Strategy override: {args.strategy}")
    logger.info(f"Testnet mode: {args.testnet}")
    logger.info(
        f"API mode: {'disabled' if args.no_api else f'http://{args.host}:{args.port}'}"
    )

    if args.no_api:
        # Legacy mode: run the bot directly without the API server
        import asyncio
        from bot import AkibaCryptoBot

        async def run_bot():
            bot = AkibaCryptoBot(config_path=args.config)

            if args.testnet:
                bot.config["exchange"]["sandbox"] = True
            if args.dry_run:
                bot.config["trading"]["dry_run"] = True

            if args.strategy and args.strategy != bot.strategy_manager.active_strategy:
                try:
                    bot.strategy_manager.add_strategy_by_name(args.strategy, bot.config)
                    bot.strategy_manager.set_active_strategy(args.strategy)
                    logger.info(f"Successfully loaded strategy: {args.strategy}")
                except Exception as e:
                    logger.error(f"Failed to load strategy {args.strategy}: {e}")
            elif args.strategy:
                logger.info(f"Strategy '{args.strategy}' already active from config")

            logger.info(f"Active strategy: {bot.strategy_manager.active_strategy}")
            log_runtime_visibility(logger, bot)
            await bot.start()

        try:
            asyncio.run(run_bot())
        except ValueError as e:
            logger.error(f"Startup validation failed: {e}")
            raise SystemExit(1) from e
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")
        finally:
            logger.info("AkibaCryptoBot shutdown complete")
    else:
        # FastAPI mode: run bot + API server together
        from api.app import create_app

        app = create_app(
            config_path=args.config,
            strategy_override=args.strategy,
            testnet=args.testnet,
        )

        logger.info(f"API docs available at: http://{args.host}:{args.port}/docs")
        logger.info("Starting trading bot + API server...")

        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info" if not args.debug else "debug",
        )


if __name__ == "__main__":
    main()
