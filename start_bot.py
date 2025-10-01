#!/usr/bin/env python3
"""
Start script for AkibaCryptoBot with FreqAI support
"""

import sys
import os
import argparse
import asyncio
import logging

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from bot import AkibaCryptoBot


def setup_logging(debug=False):
    """Setup basic logging configuration"""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('logs/akiba_crypto_bot_startup.log')
        ]
    )
    return logging.getLogger(__name__)


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='AkibaCryptoBot - Automated Cryptocurrency Trading Bot with FreqAI Support',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  start_bot.py                              # Start with default config
  start_bot.py --strategy freqai_ml_freqai  # Start with FreqAI ML strategy
  start_bot.py --testnet --debug           # Start in testnet mode with debug logging
  start_bot.py --config custom_config.json # Start with custom config
        """
    )
    
    parser.add_argument(
        '--config',
        type=str,
        default='config/config.json',
        help='Path to configuration file (default: config/config.json)'
    )
    
    parser.add_argument(
        '--strategy',
        type=str,
        help='Override strategy from config file (e.g., momentum, freqai_example_freqai, freqai_ml_freqai)'
    )
    
    parser.add_argument(
        '--testnet',
        action='store_true',
        help='Run in testnet mode (paper trading)'
    )
    
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Run in dry-run mode (no actual trades)'
    )
    
    parser.add_argument(
        '--train-model',
        action='store_true',
        help='Train FreqAI model before starting (for FreqAI strategies)'
    )
    
    parser.add_argument(
        '--model-path',
        type=str,
        help='Path to load/save FreqAI model'
    )
    
    return parser.parse_args()


async def main():
    """Main entry point"""
    # Parse command line arguments
    args = parse_arguments()
    
    # Setup logging
    logger = setup_logging(args.debug)
    
    logger.info("=" * 50)
    logger.info("Starting AkibaCryptoBot with FreqAI Support")
    logger.info("=" * 50)
    logger.info(f"Configuration file: {args.config}")
    logger.info(f"Strategy override: {args.strategy}")
    logger.info(f"Testnet mode: {args.testnet}")
    logger.info(f"Dry-run mode: {args.dry_run}")
    logger.info(f"Train model: {args.train_model}")
    logger.info(f"Model path: {args.model_path}")
    
    try:
        # Initialize the bot
        bot = AkibaCryptoBot(config_path=args.config)
        
        # Override settings based on command line arguments
        if args.testnet:
            logger.info("Setting testnet mode")
            bot.config['exchange']['sandbox'] = True
        
        if args.dry_run:
            logger.info("Setting dry-run mode")
            bot.config['trading']['dry_run'] = True
            
        # Override strategy if specified
        if args.strategy:
            logger.info(f"Overriding strategy to: {args.strategy}")
            try:
                # Try to load the specified strategy
                bot.strategy_manager.add_strategy_by_name(args.strategy, bot.config)
                bot.strategy_manager.set_active_strategy(args.strategy)
                logger.info(f"Successfully loaded strategy: {args.strategy}")
            except Exception as e:
                logger.error(f"Failed to load strategy {args.strategy}: {e}")
                logger.info("Falling back to default strategy")
        
        # Train model if requested (for FreqAI strategies)
        if args.train_model:
            logger.info("Model training requested - this feature needs to be implemented in the bot")
            # This would require extending the bot to support model training
        
        logger.info(f"Active strategy: {bot.strategy_manager.active_strategy}")
        
        # Start the bot
        logger.info("Starting trading bot...")
        await bot.start()
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down gracefully...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("AkibaCryptoBot shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())