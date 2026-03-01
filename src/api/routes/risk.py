"""
Risk Management endpoints
"""

from fastapi import APIRouter, Depends
from api.dependencies import get_bot, get_risk_manager

router = APIRouter()


@router.get("/risk")
async def get_risk_report(risk_manager=Depends(get_risk_manager)):
    """Get comprehensive risk report (Kelly criterion, ATR, drawdown)."""
    return risk_manager.get_risk_report()


@router.get("/correlations")
async def get_correlations(bot=Depends(get_bot)):
    """Get asset correlation matrix for currently tracked pairs."""
    if bot.price_data_cache:
        correlations = bot.advanced_risk.calculate_correlations(bot.price_data_cache)
        # Convert tuple keys to string keys for JSON serialization
        return {
            "correlations": {
                f"{k[0]}_{k[1]}": round(v, 4) for k, v in correlations.items()
            }
        }
    return {"correlations": {}, "message": "No price data cached yet"}


@router.get("/heatmap")
async def get_portfolio_heatmap(bot=Depends(get_bot)):
    """Get portfolio exposure heatmap across correlated assets."""
    if bot.price_data_cache and bot.active_trades:
        try:
            balance_info = await bot.get_balance_info()
            total_balance = sum(balance_info.values()) if balance_info else 10000.0

            heatmap = bot.advanced_risk.generate_portfolio_heatmap(
                positions=bot.active_trades,
                price_data=bot.price_data_cache,
                balance=total_balance,
            )
            return {
                "timestamp": heatmap.timestamp.isoformat(),
                "total_portfolio_risk": heatmap.total_portfolio_risk,
                "asset_exposures": heatmap.asset_exposures,
            }
        except Exception as e:
            return {"error": str(e)}

    return {"message": "No active positions or price data available for heatmap"}


@router.get("/circuit-breaker")
async def get_circuit_breaker_status(bot=Depends(get_bot)):
    """Get the current circuit breaker status."""
    return {
        "active": bot.circuit_breaker_active,
        "reason": bot.circuit_breaker_reason,
    }
