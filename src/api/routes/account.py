"""
Account & Configuration endpoints
"""

from fastapi import APIRouter, Depends
from api.dependencies import get_bot

router = APIRouter()


@router.get("/balance")
async def get_balance(bot=Depends(get_bot)):
    """Get live wallet balances from Binance."""
    return await bot.get_balance_info()


@router.get("/config")
async def get_config(bot=Depends(get_bot)):
    """Get current bot configuration (trading mode, risk params, pairs)."""
    return bot.get_config_info()
