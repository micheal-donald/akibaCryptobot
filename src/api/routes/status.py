"""
Status & Health endpoints
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from api.dependencies import get_bot
from runtime.rollout import build_rollout_summary
from runtime.view_models import ProjectionSource

router = APIRouter()


def _inject_runtime_visibility(status_info, bot):
    visibility_getter = getattr(bot, "get_runtime_visibility_info", None)
    if not callable(visibility_getter):
        return status_info

    status_info.update(visibility_getter())
    return status_info


@router.get("/health")
async def health_check():
    """Liveness probe ? always returns OK if the server is up."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/status")
async def get_status(bot=Depends(get_bot)):
    """Get comprehensive bot status including runtime cutover state."""
    status_info = bot.get_status_info()
    _inject_runtime_visibility(status_info, bot)
    projection = bot.get_runtime_projection()
    rollout_summary = build_rollout_summary(projection)
    status_info["circuit_breaker_active"] = bot.circuit_breaker_active
    status_info["circuit_breaker_reason"] = bot.circuit_breaker_reason
    status_info["strategy"] = bot.strategy_manager.active_strategy
    status_info["trading_mode"] = (
        "paper" if bot.config["exchange"].get("sandbox", True) else "live"
    )
    status_info["state_source"] = projection.source.value
    status_info["runtime_mode"] = projection.runtime_mode
    status_info["runtime_is_running"] = projection.runtime_is_running
    status_info["runtime_start_status"] = projection.runtime_start_status
    status_info["runtime_start_pending"] = projection.runtime_start_pending
    status_info["runtime_owns_entries"] = projection.runtime_owns_entries
    status_info["runtime_owns_exits"] = projection.runtime_owns_exits
    status_info.update(rollout_summary)
    status_info["projected_active_positions"] = projection.active_position_count
    status_info["projected_open_orders"] = projection.open_order_count
    status_info["projected_pending_exits"] = projection.pending_exit_count
    status_info["projected_rejected_orders"] = projection.rejected_order_count
    if projection.source is ProjectionSource.RUNTIME_PROJECTION:
        status_info["active_trades"] = projection.active_position_count
    return status_info


@router.get("/reconcile")
async def reconcile_status(bot=Depends(get_bot)):
    """Manually trigger and return a reconciliation check between DB and Exchange."""
    return await bot.reconciliation.reconcile()
