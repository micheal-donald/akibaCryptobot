"""
Strategy endpoints
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from api.dependencies import get_bot, get_strategy_manager

router = APIRouter()


class StrategySwitchRequest(BaseModel):
    strategy_name: str


@router.get("/strategy")
async def get_active_strategy(bot=Depends(get_bot)):
    """Get the currently active strategy and its component status."""
    active = bot.strategy_manager.active_strategy
    strategy_obj = bot.strategy_manager.get_active_strategy()

    result = {
        "active_strategy": active,
        "available_strategies": list(bot.strategy_manager.strategies.keys()),
    }

    # If the active strategy is the ensemble, show component details
    if strategy_obj and hasattr(strategy_obj, "get_strategy_status"):
        result["components"] = strategy_obj.get_strategy_status()

    return result


@router.post("/strategy/switch")
async def switch_strategy(
    request: StrategySwitchRequest,
    bot=Depends(get_bot),
    manager=Depends(get_strategy_manager),
):
    """Hot-swap the active trading strategy at runtime."""
    target = request.strategy_name

    # If the strategy isn't loaded yet, try to load it
    if target not in manager.strategies:
        try:
            manager.add_strategy_by_name(target, bot.config)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to load strategy '{target}': {str(e)}",
            )

    # Check it actually loaded
    if target not in manager.strategies:
        raise HTTPException(
            status_code=400,
            detail=f"Strategy '{target}' could not be found or loaded",
        )

    manager.set_active_strategy(target)
    return {
        "status": "switched",
        "active_strategy": manager.active_strategy,
        "available_strategies": list(manager.strategies.keys()),
    }
