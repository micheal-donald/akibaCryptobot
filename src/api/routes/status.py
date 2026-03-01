"""
Status & Health endpoints
"""

from datetime import datetime
from fastapi import APIRouter, Depends
from api.dependencies import get_bot

router = APIRouter()


@router.get("/health")
async def health_check():
    """Liveness probe — always returns OK if the server is up."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@router.get("/status")
async def get_status(bot=Depends(get_bot)):
    """Get comprehensive bot status including circuit breaker state."""
    status_info = await bot.get_status_info()
    status_info["circuit_breaker_active"] = bot.circuit_breaker_active
    status_info["circuit_breaker_reason"] = bot.circuit_breaker_reason
    status_info["strategy"] = bot.strategy_manager.active_strategy
    status_info["trading_mode"] = (
        "paper" if bot.config["exchange"].get("sandbox", True) else "live"
    )
    return status_info
