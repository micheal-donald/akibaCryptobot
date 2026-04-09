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


def _inject_runtime_telemetry(status_info, bot):
    telemetry_getter = getattr(bot, "get_runtime_telemetry_info", None)
    if not callable(telemetry_getter):
        status_info["runtime_telemetry"] = None
        return status_info

    status_info["runtime_telemetry"] = telemetry_getter()
    return status_info


def _inject_native_shadow_report(status_info, bot):
    if "native_shadow_report" in status_info:
        return status_info

    report_getter = getattr(bot, "get_native_shadow_report", None)
    if not callable(report_getter):
        status_info.setdefault("native_shadow_report", None)
        return status_info

    status_info["native_shadow_report"] = report_getter()
    return status_info


def _inject_runtime_recovery(status_info, bot):
    getter = getattr(bot, "get_runtime_recovery_info", None)
    if not callable(getter):
        status_info["runtime_recovery"] = None
        return status_info

    status_info["runtime_recovery"] = getter()
    return status_info


async def _inject_training_job(status_info, bot):
    training_getter = getattr(bot, "get_training_job_status", None)
    if not callable(training_getter):
        status_info["training_job"] = None
        return status_info

    snapshot = await training_getter()
    status_info["training_job"] = snapshot.to_dict()
    return status_info


def _inject_operational_alert_state(status_info, bot):
    getter = getattr(bot, "get_operational_alert_state", None)
    if callable(getter):
        alert_state = getter()
        if isinstance(alert_state, dict):
            status_info.setdefault(
                "operational_alerts",
                alert_state.get("operational_alerts", []),
            )
            status_info.setdefault("halt_state", alert_state.get("halt_state", "none"))
            status_info.setdefault(
                "entries_blocked_reason",
                alert_state.get("entries_blocked_reason"),
            )
    status_info.setdefault("operational_alerts", [])
    status_info.setdefault("halt_state", "none")
    status_info.setdefault("entries_blocked_reason", None)
    status_info.setdefault("active_critical_alert_count", 0)
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
    _inject_runtime_telemetry(status_info, bot)
    _inject_native_shadow_report(status_info, bot)
    _inject_runtime_recovery(status_info, bot)
    _inject_operational_alert_state(status_info, bot)
    await _inject_training_job(status_info, bot)
    projection = bot.get_runtime_projection()
    status_info["circuit_breaker_active"] = bot.circuit_breaker_active
    status_info["circuit_breaker_reason"] = bot.circuit_breaker_reason
    status_info["strategy"] = bot.strategy_manager.active_strategy
    status_info["trading_mode"] = (
        "paper" if bot.config["exchange"].get("sandbox", True) else "live"
    )
    rollout_summary = build_rollout_summary(
        projection,
        configured_deployment_profile=status_info.get("configured_deployment_profile"),
        trading_mode=status_info["trading_mode"],
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
