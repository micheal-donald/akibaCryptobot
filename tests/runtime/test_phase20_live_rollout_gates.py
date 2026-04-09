from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from api.routes.model import TrainModelRequest, start_model_training
from api.routes.status import get_status
from operations.training_jobs import TrainingJobSnapshot
from runtime.rollout import build_rollout_summary
from runtime.view_models import (
    ProjectedPositionView,
    ProjectionSource,
    RuntimeProjection,
)


NOW = datetime(2026, 4, 9, 8, 30, tzinfo=timezone.utc)


def make_position(symbol: str = "BTC/USDT") -> ProjectedPositionView:
    return ProjectedPositionView(
        position_id="pos-1",
        symbol=symbol,
        side="buy",
        status="open",
        open_qty=0.25,
        requested_qty=0.25,
        avg_entry_price=65000.0,
        avg_exit_price=None,
        stop_loss=64000.0,
        take_profit=67000.0,
        trailing_stop=None,
        entry_order_ids=("entry-1",),
        protective_order_ids=(),
        fees_paid=0.0,
        opened_at=NOW,
        closed_at=None,
        updated_at=NOW,
    )


def make_projection(
    *,
    source: ProjectionSource,
    runtime_mode: str,
    runtime_is_running: bool = True,
    runtime_is_healthy: bool = True,
    runtime_start_pending: bool = False,
    runtime_owns_entries: bool = False,
    runtime_owns_exits: bool = False,
) -> RuntimeProjection:
    return RuntimeProjection(
        source=source,
        runtime_mode=runtime_mode,
        runtime_enabled=runtime_mode != "legacy",
        runtime_is_running=runtime_is_running,
        runtime_is_healthy=runtime_is_healthy,
        runtime_owns_entries=runtime_owns_entries,
        runtime_owns_exits=runtime_owns_exits,
        active_position_count=1 if runtime_owns_entries else 0,
        protected_position_count=0,
        pending_exit_count=1 if runtime_owns_exits else 0,
        open_order_count=2 if runtime_owns_entries else 0,
        rejected_order_count=0,
        positions=(make_position(),) if runtime_owns_entries else (),
        orders=(),
        runtime_start_status="starting" if runtime_start_pending else "running",
        runtime_start_pending=runtime_start_pending,
    )


class FakeLiveRolloutBot:
    def __init__(
        self,
        projection: RuntimeProjection,
        *,
        configured_deployment_profile: str,
        alert_state: dict[str, object] | None = None,
    ) -> None:
        self._projection = projection
        self._configured_deployment_profile = configured_deployment_profile
        self._alert_state = alert_state or {
            "operational_alerts": [],
            "halt_state": "none",
            "entries_blocked_reason": None,
        }
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.strategy_manager = type(
            "StrategyManager", (), {"active_strategy": "ensemble"}
        )()
        self.config = {"exchange": {"sandbox": False}}
        self._training_snapshot = TrainingJobSnapshot(
            current_job=None,
            last_job=None,
            can_start_training=False,
            active_job_count=0,
        )

    def get_status_info(self):
        return {
            "active_trades": 0,
            "daily_pnl": 0.0,
            "uptime": "0:10:00",
            "is_running": True,
            "trading_paused": False,
            "trading_mode": "live",
        }

    def get_runtime_projection(self) -> RuntimeProjection:
        return self._projection

    def get_runtime_visibility_info(self):
        return {
            "configured_deployment_profile": self._configured_deployment_profile,
            "effective_runtime_mode": self._projection.runtime_mode,
            "effective_runtime_enabled": self._projection.runtime_enabled,
            "effective_runtime_owns_entries": self._projection.runtime_owns_entries,
            "effective_runtime_owns_exits": self._projection.runtime_owns_exits,
            "runtime_mode_source": "deployment_profile",
        }

    def get_operational_alert_state(self):
        return self._alert_state

    async def get_training_job_status(self):
        return self._training_snapshot

    async def can_start_training(self):
        return False, "training_not_allowed_in_live"

    async def enqueue_training_job(self, **kwargs):
        del kwargs
        raise AssertionError("enqueue_training_job should not be called in live mode")


@pytest.mark.asyncio
async def test_phase20_live_shadow_preflight_and_soak_criteria():
    projection = make_projection(
        source=ProjectionSource.SHADOW,
        runtime_mode="shadow",
        runtime_is_running=True,
        runtime_is_healthy=True,
    )
    bot = FakeLiveRolloutBot(
        projection,
        configured_deployment_profile="live_shadow",
    )

    payload = await get_status(bot=bot)
    summary = build_rollout_summary(
        projection,
        configured_deployment_profile="live_shadow",
        trading_mode="live",
    )

    assert payload["rollout_environment"] == "live"
    assert payload["rollout_stage"] == "canary"
    assert payload["rollout_health"] == "shadow_ready"
    assert payload["rollout_recommended_action"] == "promote_live_runtime_entries"
    assert payload["runtime_is_running"] is True
    assert payload["runtime_is_healthy"] is True
    assert payload["training_job"]["can_start_training"] is False
    assert summary["rollout_health"] == "shadow_ready"
    assert summary["rollout_recommended_action"] == "promote_live_runtime_entries"


@pytest.mark.asyncio
async def test_phase20_live_runtime_entries_preflight_and_alert_gate():
    projection = make_projection(
        source=ProjectionSource.RUNTIME_PROJECTION,
        runtime_mode="runtime_entries",
        runtime_is_running=True,
        runtime_is_healthy=True,
        runtime_owns_entries=True,
    )
    bot = FakeLiveRolloutBot(
        projection,
        configured_deployment_profile="live_runtime_entries",
        alert_state={
            "operational_alerts": [
                {
                    "code": "runtime_feed_stale",
                    "severity": "critical",
                    "source": "telemetry",
                    "message": "Feed is stale.",
                    "recommended_action": "inspect_market_data_feed",
                    "active": True,
                }
            ],
            "halt_state": "runtime_kill_switch",
            "entries_blocked_reason": "runtime_feed_stale",
        },
    )

    payload = await get_status(bot=bot)
    summary = build_rollout_summary(
        projection,
        configured_deployment_profile="live_runtime_entries",
        trading_mode="live",
        halt_state="runtime_kill_switch",
    )

    assert payload["rollout_stage"] == "canary"
    assert payload["rollout_health"] == "promotion_blocked"
    assert payload["rollout_recommended_action"] == "clear_halt_before_promotion"
    assert payload["halt_state"] == "runtime_kill_switch"
    assert payload["entries_blocked_reason"] == "runtime_feed_stale"
    assert payload["operational_alerts"][0]["code"] == "runtime_feed_stale"
    assert summary["rollout_health"] == "promotion_blocked"
    assert summary["rollout_recommended_action"] == "clear_halt_before_promotion"


@pytest.mark.asyncio
async def test_phase20_live_runtime_primary_preflight_and_status_alignment():
    projection = make_projection(
        source=ProjectionSource.RUNTIME_PROJECTION,
        runtime_mode="runtime_primary",
        runtime_is_running=True,
        runtime_is_healthy=True,
        runtime_owns_entries=True,
        runtime_owns_exits=True,
    )
    bot = FakeLiveRolloutBot(
        projection,
        configured_deployment_profile="live_runtime_primary",
    )

    payload = await get_status(bot=bot)
    summary = build_rollout_summary(
        projection,
        configured_deployment_profile="live_runtime_primary",
        trading_mode="live",
    )

    assert payload["rollout_stage"] == "primary"
    assert payload["rollout_health"] == "promotion_ready"
    assert payload["rollout_recommended_action"] == "stay_live_runtime_primary"
    assert payload["runtime_is_running"] is True
    assert payload["runtime_is_healthy"] is True
    assert payload["training_job"]["can_start_training"] is False
    assert summary["rollout_health"] == "promotion_ready"
    assert summary["rollout_recommended_action"] == "stay_live_runtime_primary"


@pytest.mark.asyncio
async def test_phase20_live_model_training_is_rejected_by_api_route():
    projection = make_projection(
        source=ProjectionSource.RUNTIME_PROJECTION,
        runtime_mode="runtime_primary",
        runtime_is_running=True,
        runtime_is_healthy=True,
        runtime_owns_entries=True,
        runtime_owns_exits=True,
    )
    bot = FakeLiveRolloutBot(
        projection,
        configured_deployment_profile="live_runtime_primary",
    )

    with pytest.raises(HTTPException) as exc:
        await start_model_training(
            TrainModelRequest(pairs=["BTC/USDT"], candles=1000, timeframe="1h"),
            bot=bot,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "training_not_allowed_in_live"
