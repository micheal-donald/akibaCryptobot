from __future__ import annotations

from datetime import datetime, timezone

import pytest

from notifications.telegram_bot import TelegramNotifier
from runtime.config import RuntimeConfig
from runtime.rollout import build_rollout_summary
from runtime.view_models import (
    ProjectedPositionView,
    ProjectionSource,
    RuntimeProjection,
)


NOW = datetime(2026, 4, 9, 8, 45, tzinfo=timezone.utc)


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, parse_mode=None):
        self.calls.append({"text": text, "parse_mode": parse_mode})


class FakeUpdate:
    def __init__(self):
        self.message = FakeMessage()


class FakeTelegramBot:
    def __init__(
        self,
        projection: RuntimeProjection,
        *,
        configured_deployment_profile: str,
        trading_mode: str = "live",
        alert_state: dict[str, object] | None = None,
    ) -> None:
        self._projection = projection
        self._configured_deployment_profile = configured_deployment_profile
        self._alert_state = alert_state or {
            "operational_alerts": [],
            "halt_state": "none",
            "entries_blocked_reason": None,
        }
        self.strategy_manager = type(
            "StrategyManager", (), {"active_strategy": "ensemble"}
        )()
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.advanced_risk = type(
            "AdvancedRisk",
            (),
            {
                "get_risk_report": lambda self: {
                    "position_sizing_parameters": {
                        "atr_period": 14,
                        "atr_multiplier": 2.0,
                        "kelly_enabled": True,
                        "kelly_cap": 0.25,
                    },
                    "correlation_parameters": {
                        "threshold": 0.8,
                        "max_correlated_positions": 2,
                    },
                    "circuit_breaker_parameters": {
                        "volatility_threshold": 0.03,
                        "drawdown_threshold": 0.2,
                    },
                    "portfolio_heatmap": {"total_risk": 12.5},
                }
            },
        )()
        self.config = {"exchange": {"sandbox": trading_mode != "live"}}
        self.is_running = True
        self.trading_paused = True

    def get_status_info(self):
        return {
            "active_trades": 0,
            "daily_pnl": 0.0,
            "uptime": "0:15:00",
            "trading_mode": "live" if not self.config["exchange"]["sandbox"] else "paper",
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
        return {
            "current_job": None,
            "last_job": None,
            "can_start_training": False,
            "active_job_count": 0,
        }


def make_projection(
    *,
    runtime_mode: str,
    runtime_is_healthy: bool = True,
    runtime_start_pending: bool = False,
    runtime_owns_entries: bool = False,
    runtime_owns_exits: bool = False,
) -> RuntimeProjection:
    return RuntimeProjection(
        source=ProjectionSource.RUNTIME_PROJECTION
        if runtime_mode != "shadow"
        else ProjectionSource.SHADOW,
        runtime_mode=runtime_mode,
        runtime_enabled=runtime_mode != "legacy",
        runtime_is_running=not runtime_start_pending,
        runtime_is_healthy=runtime_is_healthy,
        runtime_owns_entries=runtime_owns_entries,
        runtime_owns_exits=runtime_owns_exits,
        active_position_count=1 if runtime_owns_entries else 0,
        protected_position_count=0,
        pending_exit_count=1 if runtime_owns_exits else 0,
        open_order_count=1 if runtime_owns_entries else 0,
        rejected_order_count=0,
        positions=(
            ProjectedPositionView(
                position_id="pos-1",
                symbol="BTC/USDT",
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
            ),
        )
        if runtime_owns_entries
        else (),
        orders=(),
        runtime_start_status="starting" if runtime_start_pending else "running",
        runtime_start_pending=runtime_start_pending,
    )


def test_phase20_live_shadow_startup_pending_is_not_a_rollback_signal():
    projection = make_projection(
        runtime_mode="shadow",
        runtime_start_pending=True,
    )

    summary = build_rollout_summary(
        projection,
        configured_deployment_profile="live_shadow",
        trading_mode="live",
    )

    assert summary["rollout_health"] == "startup_pending"
    assert summary["rollout_recommended_action"] == "wait_for_live_runtime"


def test_phase20_live_shadow_unhealthy_rolls_back_to_live_legacy():
    projection = make_projection(runtime_mode="shadow", runtime_is_healthy=False)

    summary = build_rollout_summary(
        projection,
        configured_deployment_profile="live_shadow",
        trading_mode="live",
    )

    assert summary["rollout_health"] == "rollback_recommended"
    assert summary["rollout_recommended_action"] == "rollback_to_live_legacy"


def test_phase20_live_runtime_entries_unhealthy_rolls_back_to_live_legacy():
    projection = make_projection(
        runtime_mode="runtime_entries",
        runtime_is_healthy=False,
        runtime_owns_entries=True,
    )

    summary = build_rollout_summary(
        projection,
        configured_deployment_profile="live_runtime_entries",
        trading_mode="live",
    )

    assert summary["rollout_stage"] == "canary"
    assert summary["rollout_health"] == "rollback_recommended"
    assert summary["rollout_recommended_action"] == "rollback_to_live_legacy"


def test_phase20_live_runtime_primary_unhealthy_rolls_back_to_live_legacy():
    projection = make_projection(
        runtime_mode="runtime_primary",
        runtime_is_healthy=False,
        runtime_owns_entries=True,
        runtime_owns_exits=True,
    )

    summary = build_rollout_summary(
        projection,
        configured_deployment_profile="live_runtime_primary",
        trading_mode="live",
    )

    assert summary["rollout_stage"] == "primary"
    assert summary["rollout_health"] == "rollback_recommended"
    assert summary["rollout_recommended_action"] == "rollback_to_live_legacy"


def test_phase20_live_legacy_remains_the_safe_rollback_target():
    config = RuntimeConfig.from_mapping(
        {
            "exchange": {"sandbox": False},
            "runtime": {
                "deployment_profile": "live_legacy",
                "runtime_mode": "legacy",
                "allow_live_runtime": False,
            },
        }
    )

    config.validate_startup(exchange_sandbox=False)

    projection = RuntimeProjection(
        source=ProjectionSource.LEGACY,
        runtime_mode="legacy",
        runtime_enabled=False,
        runtime_is_running=True,
        runtime_is_healthy=True,
        runtime_owns_entries=False,
        runtime_owns_exits=False,
        active_position_count=0,
        protected_position_count=0,
        pending_exit_count=0,
        open_order_count=0,
        rejected_order_count=0,
        positions=(),
        orders=(),
    )

    summary = build_rollout_summary(
        projection,
        configured_deployment_profile="live_legacy",
        trading_mode="live",
    )

    assert summary["rollout_health"] == "legacy"
    assert summary["rollout_recommended_action"] == "stay_live_legacy"


@pytest.mark.asyncio
async def test_phase20_live_telegram_status_risk_and_training_refusal_align():
    projection = make_projection(
        runtime_mode="runtime_primary",
        runtime_is_healthy=False,
        runtime_owns_entries=True,
        runtime_owns_exits=True,
    )
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = FakeTelegramBot(
        projection,
        configured_deployment_profile="live_runtime_primary",
        alert_state={
            "operational_alerts": [
                {
                    "code": "runtime_feed_stale",
                    "severity": "critical",
                    "source": "telemetry",
                    "message": "Feed is stale.",
                    "recommended_action": "inspect_market_data_feed",
                }
            ],
            "halt_state": "runtime_kill_switch",
            "entries_blocked_reason": "runtime_feed_stale",
        },
    )

    status_update = FakeUpdate()
    risk_update = FakeUpdate()
    train_update = FakeUpdate()

    await notifier.cmd_status(status_update, None)
    await notifier.cmd_risk(risk_update, None)
    await notifier.cmd_train_model(train_update, type("Context", (), {"args": []})())

    status_message = status_update.message.calls[0]["text"]
    risk_message = risk_update.message.calls[0]["text"]
    train_message = train_update.message.calls[0]["text"]

    assert "Deployment Profile: live_runtime_primary" in status_message
    assert "Rollout Environment: live" in status_message
    assert "Rollout Stage: primary" in status_message
    assert "Rollout Recommended Action: rollback_to_live_legacy" in status_message
    assert "Halt State: runtime_kill_switch" in risk_message
    assert "Blocked Reason: runtime_feed_stale" in risk_message
    assert "Critical Alerts: runtime_feed_stale" in risk_message
    assert "Training is paper-only" in train_message
