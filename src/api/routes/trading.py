"""
Trading Control endpoints
"""

from fastapi import APIRouter, Depends

from api.dependencies import get_bot
from runtime.view_models import ProjectionSource

router = APIRouter()


@router.get("/trades")
async def get_active_trades(bot=Depends(get_bot)):
    """Get open positions from the active trading authority."""
    projection = bot.get_runtime_projection()
    if projection.source is ProjectionSource.RUNTIME_PROJECTION:
        trades = [
            {
                "position_id": position.position_id,
                "symbol": position.symbol,
                "side": position.side,
                "amount": position.open_qty,
                "entry_price": position.avg_entry_price,
                "stop_loss": position.stop_loss,
                "take_profit": position.take_profit,
                "trailing_stop_price": position.trailing_stop,
                "status": position.status,
                "source": projection.source.value,
            }
            for position in projection.positions
            if position.open_qty > 0
        ]
        return {
            "count": len(trades),
            "trades": trades,
            "source": projection.source.value,
            "runtime_mode": projection.runtime_mode,
        }

    trades = bot.get_active_trades()
    response = {
        "count": len(trades),
        "trades": trades,
        "source": "legacy",
        "runtime_mode": projection.runtime_mode,
    }
    if projection.source is ProjectionSource.SHADOW:
        response["shadow_projected_count"] = projection.active_position_count
    return response


@router.post("/trading/pause")
async def pause_trading(bot=Depends(get_bot)):
    """Pause the trading engine (stops scanning for new opportunities)."""
    bot.pause_trading()
    return {
        "status": "paused",
        "is_running": bot.is_running,
        "trading_paused": getattr(bot, "trading_paused", False),
    }


@router.post("/trading/resume")
async def resume_trading(bot=Depends(get_bot)):
    """Resume the trading engine."""
    bot.resume_trading()
    return {
        "status": "resumed",
        "is_running": bot.is_running,
        "trading_paused": getattr(bot, "trading_paused", False),
    }
