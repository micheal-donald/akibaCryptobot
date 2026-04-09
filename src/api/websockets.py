"""
WebSocket streams for real-time data push to browser clients.
"""

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.dependencies import bot_state
from runtime.native_bridge import build_native_shadow_report
from runtime.rollout import build_rollout_summary
from runtime.view_models import ProjectionSource

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_runtime_projection(bot):
    projection_getter = getattr(bot, "get_runtime_projection", None)
    if projection_getter is None:
        return None
    try:
        return projection_getter()
    except Exception as exc:
        logger.error("WebSocket runtime projection lookup failed: %s", exc)
        return None


def _build_native_shadow_canary_report(bot):
    runtime_app = getattr(bot, "runtime_app", None)
    if runtime_app is None or getattr(runtime_app, "native_hot_path_ports", None) is None:
        return None

    projection = _get_runtime_projection(bot)
    return build_native_shadow_report(bot, projection=projection)


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
    canary_report = _build_native_shadow_canary_report(bot)
    if canary_report is not None:
        status_info["native_shadow_report"] = canary_report
        return status_info

    if "native_shadow_report" in status_info:
        return status_info

    report_getter = getattr(bot, "get_native_shadow_report", None)
    if not callable(report_getter):
        status_info.setdefault("native_shadow_report", None)
        return status_info

    status_info["native_shadow_report"] = report_getter()
    return status_info


async def _inject_training_job(status_info, bot):
    training_getter = getattr(bot, "get_training_job_status", None)
    if not callable(training_getter):
        status_info["training_job"] = None
        return status_info

    snapshot = await training_getter()
    status_info["training_job"] = snapshot.to_dict() if snapshot is not None else None
    return status_info


async def _inject_live_rollout_approval(status_info, bot):
    getter = getattr(bot, "get_live_rollout_approval_status", None)
    if not callable(getter):
        status_info["live_rollout_approval"] = None
        status_info["latest_live_rollout_event"] = None
        return status_info

    snapshot = await getter()
    if snapshot is None:
        status_info["live_rollout_approval"] = None
        status_info["latest_live_rollout_event"] = None
        return status_info
    payload = snapshot.to_dict()
    status_info["live_rollout_approval"] = payload
    status_info["latest_live_rollout_event"] = payload.get("latest_event")
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


async def _build_status_payload(bot):
    status_info = bot.get_status_info()
    _inject_runtime_visibility(status_info, bot)
    _inject_runtime_telemetry(status_info, bot)
    _inject_native_shadow_report(status_info, bot)
    _inject_operational_alert_state(status_info, bot)
    await _inject_training_job(status_info, bot)
    await _inject_live_rollout_approval(status_info, bot)
    projection = _get_runtime_projection(bot)
    rollout_summary = build_rollout_summary(
        projection,
        configured_deployment_profile=status_info.get("configured_deployment_profile"),
        trading_mode=status_info.get("trading_mode", "paper"),
        halt_state=status_info.get("halt_state"),
    )

    active_trades = status_info.get("active_trades", 0)
    runtime_mode = status_info.get("runtime_mode", "legacy")
    runtime_is_running = status_info.get("runtime_is_running", False)
    runtime_start_status = status_info.get("runtime_start_status", "disabled")
    runtime_start_pending = status_info.get("runtime_start_pending", False)
    runtime_owns_entries = status_info.get("runtime_owns_entries", False)
    runtime_owns_exits = status_info.get("runtime_owns_exits", False)
    state_source = ProjectionSource.LEGACY.value
    projected_active_positions = 0
    projected_open_orders = 0
    projected_pending_exits = 0
    projected_rejected_orders = 0

    if projection is not None:
        state_source = projection.source.value
        runtime_mode = projection.runtime_mode
        runtime_is_running = projection.runtime_is_running
        runtime_start_status = projection.runtime_start_status
        runtime_start_pending = projection.runtime_start_pending
        runtime_owns_entries = projection.runtime_owns_entries
        runtime_owns_exits = projection.runtime_owns_exits
        projected_active_positions = projection.active_position_count
        projected_open_orders = projection.open_order_count
        projected_pending_exits = projection.pending_exit_count
        projected_rejected_orders = projection.rejected_order_count

        if projection.source is ProjectionSource.RUNTIME_PROJECTION:
            active_trades = projection.active_position_count

    return {
        "type": "status",
        "data": {
            "is_running": status_info.get("is_running", True),
            "uptime": status_info.get("uptime", "Unknown"),
            "active_trades": active_trades,
            "circuit_breaker_active": bot.circuit_breaker_active,
            "circuit_breaker_reason": bot.circuit_breaker_reason,
            "strategy": bot.strategy_manager.active_strategy,
            "trading_mode": status_info.get("trading_mode", "paper"),
            "configured_deployment_profile": status_info.get(
                "configured_deployment_profile"
            ),
            "effective_runtime_mode": status_info.get(
                "effective_runtime_mode", runtime_mode
            ),
            "effective_runtime_enabled": status_info.get(
                "effective_runtime_enabled", runtime_mode != "legacy"
            ),
            "effective_runtime_owns_entries": status_info.get(
                "effective_runtime_owns_entries", runtime_owns_entries
            ),
            "effective_runtime_owns_exits": status_info.get(
                "effective_runtime_owns_exits", runtime_owns_exits
            ),
            "runtime_mode_source": status_info.get(
                "runtime_mode_source",
                "legacy_default" if runtime_mode == "legacy" else "runtime_mode",
            ),
            "runtime_mode": runtime_mode,
            "runtime_is_running": runtime_is_running,
            "runtime_start_status": runtime_start_status,
            "runtime_start_pending": runtime_start_pending,
            "runtime_owns_entries": runtime_owns_entries,
            "runtime_owns_exits": runtime_owns_exits,
            "runtime_telemetry": status_info.get("runtime_telemetry"),
            "native_shadow_report": status_info.get("native_shadow_report"),
            "training_job": status_info.get("training_job"),
            "live_rollout_approval": status_info.get("live_rollout_approval"),
            "latest_live_rollout_event": status_info.get("latest_live_rollout_event"),
            "operational_alerts": status_info.get("operational_alerts", []),
            "halt_state": status_info.get("halt_state", "none"),
            "entries_blocked_reason": status_info.get("entries_blocked_reason"),
            "active_critical_alert_count": status_info.get(
                "active_critical_alert_count", 0
            ),
            "state_source": state_source,
            **rollout_summary,
            "projected_active_positions": projected_active_positions,
            "projected_open_orders": projected_open_orders,
            "projected_pending_exits": projected_pending_exits,
            "projected_rejected_orders": projected_rejected_orders,
            "timestamp": datetime.utcnow().isoformat(),
        },
    }


@router.websocket("/ws/prices")
async def ws_prices(websocket: WebSocket):
    """
    Stream real-time price ticks from the Binance exchange cache.
    Pushes updated prices every second.
    """
    await websocket.accept()
    logger.info("WebSocket client connected to /ws/prices")

    try:
        while True:
            bot = bot_state.get("bot")
            if bot and bot.data_exchange:
                prices = bot.data_exchange.realtime_prices
                if prices:
                    await websocket.send_json(
                        {
                            "type": "prices",
                            "timestamp": datetime.utcnow().isoformat(),
                            "data": prices,
                        }
                    )
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected from /ws/prices")
    except Exception as e:
        logger.error(f"WebSocket /ws/prices error: {e}")


@router.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    """
    Stream bot status updates every 5 seconds.
    Includes active trades count, circuit breaker state, and uptime.
    """
    await websocket.accept()
    logger.info("WebSocket client connected to /ws/status")

    try:
        while True:
            bot = bot_state.get("bot")
            if bot:
                await websocket.send_json(await _build_status_payload(bot))

            await asyncio.sleep(5)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected from /ws/status")
    except Exception as e:
        logger.error(f"WebSocket /ws/status error: {e}")
