"""
Dependency injection for FastAPI routes.

Provides access to the shared bot singleton and its sub-components.
"""

from fastapi import Depends, HTTPException

# Shared mutable state — populated by the lifespan in app.py
bot_state: dict = {}


def get_bot():
    """Return the live AkibaCryptoBot instance."""
    bot = bot_state.get("bot")
    if bot is None:
        raise HTTPException(
            status_code=503, detail="Trading bot is not initialized yet"
        )
    return bot


def get_exchange(bot=Depends(get_bot)):
    """Return the BinanceClient instance used for trading (sandbox when in sandbox mode)."""
    return bot.exchange


def get_data_exchange(bot=Depends(get_bot)):
    """Return the BinanceClient used for public market data (always production)."""
    return bot.data_exchange


def get_risk_manager(bot=Depends(get_bot)):
    """Return the AdvancedRiskManager instance."""
    return bot.advanced_risk


def get_strategy_manager(bot=Depends(get_bot)):
    """Return the StrategyManager instance."""
    return bot.strategy_manager
