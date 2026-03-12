"""
Performance Metrics endpoints
"""

from fastapi import APIRouter, Depends, Query
from api.dependencies import get_bot

router = APIRouter()


@router.get("/metrics")
async def get_metrics(bot=Depends(get_bot)):
    """
    Get comprehensive performance metrics:
    Sharpe ratio, Sortino ratio, profit factor, win rate, max drawdown, etc.
    """
    if hasattr(bot, "performance_tracker") and bot.performance_tracker:
        return bot.performance_tracker.get_metrics_summary()

    # Fallback: return strategy-level performance from strategy manager
    return bot.strategy_manager.get_all_performance()


@router.get("/equity-curve")
async def get_equity_curve(bot=Depends(get_bot)):
    """Get time-series equity data for charting."""
    if hasattr(bot, "performance_tracker") and bot.performance_tracker:
        curve = bot.performance_tracker.equity_curve
        return {
            "data": [
                {
                    "timestamp": ts.isoformat(),
                    "balance": balance,
                }
                for ts, balance in curve
            ]
        }

    # Fallback: use portfolio value history
    return {
        "data": [
            {"index": i, "balance": val}
            for i, val in enumerate(bot.portfolio_value_history)
        ]
    }


@router.get("/trades/history")
async def get_trade_history(
    limit: int = Query(default=20, ge=1, le=100),
    bot=Depends(get_bot),
):
    """Get completed trade log."""
    if hasattr(bot, "performance_tracker") and bot.performance_tracker:
        trades = bot.performance_tracker.get_recent_trades(limit=limit)
        return {
            "count": len(trades),
            "trades": [
                {
                    "symbol": t.symbol,
                    "side": t.side,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "amount": t.amount,
                    "pnl": t.pnl,
                    "pnl_percent": t.pnl_percent,
                    "exit_reason": t.exit_reason,
                    "strategy": t.strategy,
                    "entry_time": t.entry_time.isoformat(),
                    "exit_time": t.exit_time.isoformat(),
                    "duration": str(t.duration),
                }
                for t in trades
            ],
        }
    return {"count": 0, "trades": []}


@router.get("/strategy/performance")
async def get_strategy_performance(bot=Depends(get_bot)):
    """Get per-strategy performance breakdown."""
    return bot.strategy_manager.get_all_performance()
