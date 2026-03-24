"""
Status & Health endpoints
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from api.dependencies import get_bot
from runtime.view_models import ProjectionSource

router = APIRouter()


def _build_rollout_summary(projection):
    """Summarize rollout health for operator-facing status surfaces."""

    runtime_is_healthy = getattr(projection, "runtime_is_healthy", True)
    runtime_owns_entries = getattr(projection, "runtime_owns_entries", False)
    runtime_owns_exits = getattr(projection, "runtime_owns_exits", False)
    state_source = getattr(projection, "source", ProjectionSource.LEGACY).value

    if state_source == ProjectionSource.LEGACY.value:
        return {
            "runtime_is_healthy": runtime_is_healthy,
            "rollout_health": "legacy",
            "rollout_recommended_action": "stay_legacy",
            "rollout_summary": "Legacy loop remains authoritative.",
        }

    if not runtime_is_healthy:
        return {
            "runtime_is_healthy": runtime_is_healthy,
            "rollout_health": "rollback_recommended",
            "rollout_recommended_action": "rollback_to_legacy",
            "rollout_summary": "Runtime is unhealthy; rollback to legacy is recommended.",
        }

    if runtime_owns_exits:
        return {
            "runtime_is_healthy": runtime_is_healthy,
            "rollout_health": "promotion_ready",
            "rollout_recommended_action": "stay_runtime_primary",
            "rollout_summary": "Runtime primary ownership is healthy and ready for operator review.",
        }

    if runtime_owns_entries:
        return {
            "runtime_is_healthy": runtime_is_healthy,
            "rollout_health": "promotion_ready",
            "rollout_recommended_action": "promote_runtime_primary",
            "rollout_summary": "Runtime entry ownership is healthy and ready for promotion.",
        }

    return {
        "runtime_is_healthy": runtime_is_healthy,
        "rollout_health": "shadow_ready",
        "rollout_recommended_action": "continue_shadow",
        "rollout_summary": "Runtime is healthy in shadow mode; validate before promotion.",
    }


@router.get("/health")
async def health_check():
    """Liveness probe ? always returns OK if the server is up."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/status")
async def get_status(bot=Depends(get_bot)):
    """Get comprehensive bot status including runtime cutover state."""
    status_info = bot.get_status_info()
    projection = bot.get_runtime_projection()
    rollout_summary = _build_rollout_summary(projection)
    status_info["circuit_breaker_active"] = bot.circuit_breaker_active
    status_info["circuit_breaker_reason"] = bot.circuit_breaker_reason
    status_info["strategy"] = bot.strategy_manager.active_strategy
    status_info["trading_mode"] = (
        "paper" if bot.config["exchange"].get("sandbox", True) else "live"
    )
    status_info["state_source"] = projection.source.value
    status_info["runtime_mode"] = projection.runtime_mode
    status_info["runtime_is_running"] = projection.runtime_is_running
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
