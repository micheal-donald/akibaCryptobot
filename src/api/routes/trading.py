"""
Trading Control endpoints
"""

from fastapi import APIRouter, Depends
from api.dependencies import get_bot

router = APIRouter()


@router.get("/trades")
async def get_active_trades(bot=Depends(get_bot)):
    """Get all currently open positions with entry prices."""
    trades = await bot.get_active_trades()
    return {"count": len(trades), "trades": trades}


@router.post("/trading/pause")
async def pause_trading(bot=Depends(get_bot)):
    """Pause the trading engine (stops scanning for new opportunities)."""
    bot.pause_trading()
    return {"status": "paused", "is_running": bot.is_running}


@router.post("/trading/resume")
async def resume_trading(bot=Depends(get_bot)):
    """Resume the trading engine."""
    bot.resume_trading()
    return {"status": "resumed", "is_running": bot.is_running}
