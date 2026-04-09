from datetime import datetime, timezone

import pytest

from notifications.telegram_bot import TelegramNotifier
from operations.training_jobs import (
    TRAINING_JOB_QUEUED,
    TRAINING_JOB_RUNNING,
    TRAINING_JOB_SUCCEEDED,
    TrainingJobRecord,
    TrainingJobSnapshot,
)
from runtime.view_models import (
    ProjectedPositionView,
    ProjectionSource,
    RuntimeProjection,
)


NOW = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, parse_mode=None):
        self.calls.append({"text": text, "parse_mode": parse_mode})


class FakeUpdate:
    def __init__(self):
        self.message = FakeMessage()


class FakePerformanceTracker:
    async def get_metrics_summary(self, trade_store=None):
        return {
            "initial_balance": 1000.0,
            "current_balance": 1010.0,
            "total_pnl": 10.0,
            "total_return_percent": 1.0,
            "total_trades": 3,
            "winning_trades": 2,
            "losing_trades": 1,
            "win_rate": 66.7,
            "average_win": 8.0,
            "average_loss": -6.0,
            "win_loss_ratio": 1.33,
            "sharpe_ratio": 1.2,
            "sortino_ratio": 1.5,
            "profit_factor": 1.8,
            "expectancy": 3.3,
            "max_drawdown_percent": 2.5,
            "current_drawdown_percent": 0.5,
            "open_trades_count": 7,
        }


class FakeTrainingStore:
    def __init__(self, snapshot=None, enqueue_result=None):
        self.snapshot = snapshot
        self.enqueue_result = enqueue_result
        self.enqueued_requests = []

    async def get_training_job_status(self):
        return self.snapshot

    async def enqueue_training_job(self, request):
        self.enqueued_requests.append(request)
        return self.enqueue_result


class FakeStrategyComponent:
    def __init__(self):
        self.model_trained = True
        self.feature_columns = ["%-rsi", "%-macd"]
        self.ml_model = type(
            "FakeModel",
            (),
            {"feature_importances_": [0.8, 0.2]},
        )()


class FakeEnsembleStrategy:
    def __init__(self):
        self.strategies = [(FakeStrategyComponent(), 0.6, "freqai")]


class FakeStrategyManager:
    def __init__(self):
        self._active_strategy = FakeEnsembleStrategy()

    def get_active_strategy(self):
        return self._active_strategy


def make_projection(
    *, source: ProjectionSource, runtime_mode: str
) -> RuntimeProjection:
    return RuntimeProjection(
        source=source,
        runtime_mode=runtime_mode,
        runtime_enabled=runtime_mode != "legacy",
        runtime_is_running=runtime_mode != "legacy",
        runtime_is_healthy=True,
        runtime_owns_entries=runtime_mode in {"runtime_entries", "runtime_primary"},
        runtime_owns_exits=runtime_mode == "runtime_primary",
        active_position_count=1,
        protected_position_count=0,
        pending_exit_count=1,
        open_order_count=2,
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
        ),
        orders=(),
    )


class FakeBot:
    def __init__(
        self,
        projection: RuntimeProjection,
        *,
        configured_deployment_profile: str | None = None,
        trading_mode: str = "paper",
        alert_state: dict | None = None,
        live_rollout_status: dict | None = None,
    ):
        self._projection = projection
        self.trade_store = object()
        self.performance_tracker = FakePerformanceTracker()
        self.strategy_manager = FakeStrategyManager()
        self._configured_deployment_profile = configured_deployment_profile
        self.config = {"exchange": {"sandbox": trading_mode != "live"}}
        self._trading_mode = trading_mode
        self.is_running = False
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
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self._alert_state = alert_state or {
            "operational_alerts": [],
            "halt_state": "none",
            "entries_blocked_reason": None,
        }
        self._live_rollout_status = live_rollout_status

    def get_status_info(self):
        return {
            "active_trades": 3,
            "daily_pnl": 12.34,
            "uptime": "0:10:00",
            "trading_mode": self._trading_mode,
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
            "runtime_mode_source": (
                "deployment_profile"
                if self._configured_deployment_profile is not None
                else "legacy_default"
            ),
        }

    def get_active_trades(self):
        return {
            "ETH/USDT": {
                "symbol": "ETH/USDT",
                "side": "buy",
                "amount": 1.5,
                "entry_price": 3200.0,
            }
        }

    def get_operational_alert_state(self):
        return self._alert_state

    async def get_live_rollout_approval_status(self):
        return self._live_rollout_status


class TrainingBot:
    def __init__(
        self,
        *,
        trading_mode: str = "paper",
        is_running: bool = False,
        snapshot=None,
        enqueue_result=None,
    ):
        self.config = {
            "exchange": {"sandbox": trading_mode != "live"},
            "trading": {"pairs": ["BTC/USDT", "ETH/USDT"]},
            "freqai": {"ml_params": {"training_data_period": 1200}},
        }
        self.is_running = is_running
        self.trade_store = FakeTrainingStore(
            snapshot=snapshot, enqueue_result=enqueue_result
        )
        self.strategy_manager = FakeStrategyManager()
        self._alert_state = {
            "operational_alerts": [],
            "halt_state": "none",
            "entries_blocked_reason": None,
        }

    def get_status_info(self):
        return {
            "active_trades": 0,
            "daily_pnl": 0.0,
            "uptime": "0:00:10",
            "trading_mode": "paper" if self.config["exchange"]["sandbox"] else "live",
        }

    def get_runtime_projection(self):
        return make_projection(
            source=ProjectionSource.LEGACY,
            runtime_mode="legacy",
        )

    def get_runtime_visibility_info(self):
        return {
            "configured_deployment_profile": "paper_legacy",
            "effective_runtime_mode": "legacy",
            "effective_runtime_enabled": False,
            "effective_runtime_owns_entries": False,
            "effective_runtime_owns_exits": False,
            "runtime_mode_source": "legacy_default",
        }

    def get_active_trades(self):
        return {}

    def get_operational_alert_state(self):
        return self._alert_state


@pytest.mark.asyncio
async def test_cmd_status_uses_runtime_projection_counts():
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = FakeBot(
        make_projection(
            source=ProjectionSource.RUNTIME_PROJECTION,
            runtime_mode="runtime_primary",
        )
    )
    update = FakeUpdate()

    await notifier.cmd_status(update, None)

    message = update.message.calls[0]["text"]
    assert "Active Trades: 1" in message
    assert "Runtime Mode: runtime_primary" in message
    assert "State Source: runtime_projection" in message
    assert "Rollout Environment: paper" in message
    assert "Rollout Stage: primary" in message
    assert "Runtime Healthy: yes" in message
    assert "Rollout Health: promotion_ready" in message
    assert "Rollout Recommended Action: stay_runtime_primary" in message
    assert "Rollout Summary: Runtime primary ownership is healthy" in message
    assert "Projected Open Orders: 2" in message
    assert "Projected Pending Exits: 1" in message


@pytest.mark.asyncio
async def test_cmd_status_includes_current_training_job():
    job = TrainingJobRecord(
        job_id="job-1",
        status=TRAINING_JOB_RUNNING,
        requested_by="telegram",
        pairs=("BTC/USDT",),
        candles=1200,
        timeframe="1h",
        progress=42.0,
        result_summary=None,
        error=None,
        reload_status=None,
    )
    snapshot = TrainingJobSnapshot(
        current_job=job,
        last_job=job,
        can_start_training=False,
        active_job_count=1,
    )

    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = TrainingBot(snapshot=snapshot)
    update = FakeUpdate()

    await notifier.cmd_status(update, None)

    message = update.message.calls[0]["text"]
    assert "Training Job" in message
    assert "Status: running" in message
    assert "Requested By: telegram" in message
    assert "Progress: 42%" in message


@pytest.mark.asyncio
async def test_cmd_status_includes_operational_halt_summary():
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = FakeBot(
        make_projection(
            source=ProjectionSource.RUNTIME_PROJECTION,
            runtime_mode="runtime_primary",
        ),
        configured_deployment_profile="live_runtime_primary",
        trading_mode="live",
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
    update = FakeUpdate()

    await notifier.cmd_status(update, None)

    message = update.message.calls[0]["text"]
    assert "Operational Alerts" in message
    assert "Halt State: runtime_kill_switch" in message
    assert "Entries Blocked Reason: runtime_feed_stale" in message
    assert "Rollout Health: promotion_blocked" in message
    assert "Rollout Recommended Action: clear_halt_before_continuing_live_primary" in message


@pytest.mark.asyncio
async def test_cmd_trades_uses_runtime_projected_positions():
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = FakeBot(
        make_projection(
            source=ProjectionSource.RUNTIME_PROJECTION,
            runtime_mode="runtime_primary",
        )
    )
    update = FakeUpdate()

    await notifier.cmd_trades(update, None)

    message = update.message.calls[0]["text"]
    assert "BTC/USDT: BUY 0.25 @ $65000.000000 [open]" in message
    assert "ETH/USDT" not in message
    assert "State Source: runtime_projection" in message
    assert "Rollout Environment: paper" in message
    assert "Rollout Health: promotion_ready" in message
    assert "Rollout Recommended Action: stay_runtime_primary" in message


@pytest.mark.asyncio
async def test_cmd_model_uses_model_snapshot_and_training_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "BTC_USDT_freqai.joblib").write_text("stub")

    job = TrainingJobRecord(
        job_id="job-2",
        status=TRAINING_JOB_SUCCEEDED,
        requested_by="api",
        pairs=("BTC/USDT", "ETH/USDT"),
        candles=1000,
        timeframe="1h",
        progress=100.0,
        result_summary="Reloaded",
        error=None,
        reload_status="reloaded",
    )
    snapshot = TrainingJobSnapshot(
        current_job=None,
        last_job=job,
        can_start_training=True,
        active_job_count=0,
    )

    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = TrainingBot(snapshot=snapshot)
    update = FakeUpdate()

    await notifier.cmd_model(update, None)

    message = update.message.calls[0]["text"]
    assert "🧠 Model Status" in message
    assert "Model: freqai" in message
    assert "Trained: yes" in message
    assert "Top Features: %-rsi" in message
    assert "Last Training Job" in message
    assert "Status: succeeded" in message
    assert "Reload Status: reloaded" in message


@pytest.mark.asyncio
async def test_cmd_train_model_queues_job_with_defaults_when_paused():
    job = TrainingJobRecord(
        job_id="job-3",
        status=TRAINING_JOB_QUEUED,
        requested_by="telegram",
        pairs=("BTC/USDT",),
        candles=1500,
        timeframe="4h",
    )
    snapshot = TrainingJobSnapshot(
        current_job=job,
        last_job=job,
        can_start_training=False,
        active_job_count=1,
    )

    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = TrainingBot(
        snapshot=snapshot,
        is_running=False,
        enqueue_result=job,
    )
    update = FakeUpdate()

    context = type(
        "Context", (), {"args": ["BTC/USDT", "candles=1500", "timeframe=4h"]}
    )()
    await notifier.cmd_train_model(update, context)

    message = update.message.calls[0]["text"]
    assert "Training job queued successfully." in message
    assert "Job ID: job-3" in message
    assert "Requested By: telegram" in message
    assert "Pairs: BTC/USDT" in message
    assert "Timeframe: 4h" in message
    assert "Candles: 1500" in message
    assert (
        notifier.bot_instance.trade_store.enqueued_requests[0]["requested_by"]
        == "telegram"
    )


@pytest.mark.asyncio
async def test_cmd_train_model_rejects_when_bot_is_running():
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = TrainingBot(is_running=True)
    update = FakeUpdate()
    context = type("Context", (), {"args": []})()

    await notifier.cmd_train_model(update, context)

    message = update.message.calls[0]["text"]
    assert "Pause trading before starting model training." in message


@pytest.mark.asyncio
async def test_cmd_train_status_reports_current_job():
    job = TrainingJobRecord(
        job_id="job-4",
        status=TRAINING_JOB_RUNNING,
        requested_by="api",
        pairs=("BNB/USDT",),
        candles=900,
        timeframe="30m",
        progress=55.0,
    )
    snapshot = TrainingJobSnapshot(
        current_job=job,
        last_job=job,
        can_start_training=False,
        active_job_count=1,
    )

    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = TrainingBot(snapshot=snapshot)
    update = FakeUpdate()
    context = type("Context", (), {"args": []})()

    await notifier.cmd_train_status(update, context)

    message = update.message.calls[0]["text"]
    assert "🧠 Training Control" in message
    assert "Can Start Training: no" in message
    assert "Active Jobs: 1" in message
    assert "Current Job" in message
    assert "Status: running" in message


@pytest.mark.asyncio
async def test_cmd_risk_includes_halt_reason():
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = FakeBot(
        make_projection(
            source=ProjectionSource.RUNTIME_PROJECTION,
            runtime_mode="runtime_primary",
        ),
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
    update = FakeUpdate()

    await notifier.cmd_risk(update, None)

    message = update.message.calls[0]["text"]
    assert "Entry Halt State: runtime_kill_switch" in message
    assert "Blocked Reason: runtime_feed_stale" in message
    assert "Critical Alerts: runtime_feed_stale" in message


@pytest.mark.asyncio
async def test_send_trade_notification_uses_directional_icons():
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    messages = []

    async def fake_send_message(message, parse_mode=None):
        messages.append(message)
        return True

    notifier.send_message = fake_send_message

    await notifier.send_trade_notification(
        {
            "action": "entry",
            "symbol": "BTC/USDT",
            "side": "buy",
            "amount": 0.25,
            "price": 65000.0,
        }
    )
    await notifier.send_trade_notification(
        {
            "action": "exit",
            "symbol": "BTC/USDT",
            "side": "sell",
            "amount": 0.25,
            "price": 66000.0,
            "pnl": 250.0,
            "reason": "take_profit",
        }
    )

    assert "🟢⬆️ NEW LONG ENTRY" in messages[0]
    assert "✅📈 CLOSED SELL TRADE" in messages[1]


@pytest.mark.asyncio
async def test_send_training_notification_uses_training_icons():
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    messages = []

    async def fake_send_message(message, parse_mode=None):
        messages.append(message)
        return True

    notifier.send_message = fake_send_message

    await notifier.send_training_notification(
        {
            "job_id": "job-5",
            "status": "running",
            "requested_by": "telegram",
            "pairs": ["BTC/USDT"],
            "candles": 1200,
            "timeframe": "1h",
            "progress": 37.5,
        }
    )

    assert "🧠⚙️ TRAINING JOB RUNNING" in messages[0]
    assert "Progress: 38%" in messages[0]


@pytest.mark.asyncio
async def test_send_operational_alert_transition_notifies_new_critical_alert():
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    messages = []

    async def fake_send_message(message, parse_mode=None):
        messages.append(message)
        return True

    notifier.send_message = fake_send_message

    await notifier.send_operational_alert_transition(
        {
            "operational_alerts": [],
            "halt_state": "none",
            "entries_blocked_reason": None,
        },
        {
            "operational_alerts": [
                {
                    "code": "runtime_feed_stale",
                    "severity": "critical",
                    "recommended_action": "inspect_market_data_feed",
                }
            ],
            "halt_state": "runtime_kill_switch",
            "entries_blocked_reason": "runtime_feed_stale",
        },
    )

    assert messages
    assert "OPERATIONS ALERT" in messages[0]
    assert "runtime_feed_stale" in messages[0]


@pytest.mark.asyncio
async def test_send_operational_alert_transition_notifies_recovery():
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    messages = []

    async def fake_send_message(message, parse_mode=None):
        messages.append(message)
        return True

    notifier.send_message = fake_send_message

    await notifier.send_operational_alert_transition(
        {
            "operational_alerts": [
                {"code": "runtime_feed_stale", "severity": "critical"}
            ],
            "halt_state": "runtime_kill_switch",
            "entries_blocked_reason": "runtime_feed_stale",
        },
        {
            "operational_alerts": [],
            "halt_state": "none",
            "entries_blocked_reason": None,
        },
    )

    assert messages
    assert "OPERATIONS RECOVERY" in messages[0]


@pytest.mark.asyncio
async def test_cmd_trades_keeps_legacy_state_in_shadow_mode():
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = FakeBot(
        make_projection(source=ProjectionSource.SHADOW, runtime_mode="shadow")
    )
    update = FakeUpdate()

    await notifier.cmd_trades(update, None)

    message = update.message.calls[0]["text"]
    assert "ETH/USDT: BUY 1.5 @ $3200.000000" in message
    assert "Shadow Projected Positions: 1" in message
    assert "State Source: shadow" in message
    assert "Rollout Environment: paper" in message
    assert "Rollout Health: shadow_ready" in message
    assert "Rollout Recommended Action: continue_shadow" in message


@pytest.mark.asyncio
async def test_cmd_performance_uses_runtime_open_position_count():
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = FakeBot(
        make_projection(
            source=ProjectionSource.RUNTIME_PROJECTION,
            runtime_mode="runtime_primary",
        )
    )
    update = FakeUpdate()

    await notifier.cmd_performance(update, None)

    message = update.message.calls[0]["text"]
    assert "Open Positions: 1" in message
    assert "Runtime Mode: runtime_primary" in message
    assert "Rollout Environment: paper" in message
    assert "State Source: runtime_projection" in message
    assert "Rollout Health: promotion_ready" in message


@pytest.mark.asyncio
async def test_cmd_metrics_uses_runtime_open_position_count():
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = FakeBot(
        make_projection(
            source=ProjectionSource.RUNTIME_PROJECTION,
            runtime_mode="runtime_primary",
        )
    )
    update = FakeUpdate()

    await notifier.cmd_metrics(update, None)

    message = update.message.calls[0]["text"]
    assert "Open Positions: 1" in message
    assert "Runtime Mode: runtime_primary" in message
    assert "Rollout Environment: paper" in message
    assert "State Source: runtime_projection" in message
    assert "Rollout Health: promotion_ready" in message


@pytest.mark.asyncio
async def test_cmd_status_shows_rollback_guidance_for_unhealthy_runtime():
    projection = make_projection(
        source=ProjectionSource.RUNTIME_PROJECTION,
        runtime_mode="runtime_primary",
    )
    object.__setattr__(projection, "runtime_is_healthy", False)

    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = FakeBot(projection)
    update = FakeUpdate()

    await notifier.cmd_status(update, None)

    message = update.message.calls[0]["text"]
    assert "Runtime Healthy: no" in message
    assert "Rollout Environment: paper" in message
    assert "Rollout Health: rollback_recommended" in message
    assert "Rollout Recommended Action: rollback_to_legacy" in message
    assert "rollback to legacy is recommended" in message


@pytest.mark.asyncio
async def test_cmd_status_shows_runtime_startup_pending():
    projection = make_projection(
        source=ProjectionSource.SHADOW,
        runtime_mode="shadow",
    )
    object.__setattr__(projection, "runtime_is_running", False)
    object.__setattr__(projection, "runtime_is_healthy", False)
    object.__setattr__(projection, "runtime_start_status", "starting")
    object.__setattr__(projection, "runtime_start_pending", True)

    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = FakeBot(projection)
    update = FakeUpdate()

    await notifier.cmd_status(update, None)

    message = update.message.calls[0]["text"]
    assert "Runtime Running: no" in message
    assert "Runtime Start Status: starting" in message
    assert "Runtime Start Pending: True" in message
    assert "Rollout Environment: paper" in message
    assert "Rollout Health: startup_pending" in message
    assert "Rollout Recommended Action: wait_for_runtime" in message
    assert "startup is still in progress" in message


@pytest.mark.asyncio
async def test_cmd_status_distinguishes_live_canary_rollout():
    live_rollout_status = {
        "target_profile": "live_shadow",
        "approval_required": True,
        "current_approval": {
            "approval_id": "approval-1",
            "target_profile": "live_shadow",
            "actor": "ops",
            "reason": "canary approved",
            "status": "active",
        },
        "latest_event": {
            "event_id": "event-1",
            "event_type": "approval_granted",
            "target_profile": "live_shadow",
            "actor": "ops",
            "reason": "canary approved",
        },
    }
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = FakeBot(
        make_projection(source=ProjectionSource.SHADOW, runtime_mode="shadow"),
        configured_deployment_profile="live_shadow",
        trading_mode="live",
        live_rollout_status=live_rollout_status,
    )
    update = FakeUpdate()

    await notifier.cmd_status(update, None)

    message = update.message.calls[0]["text"]
    assert "Deployment Profile: live_shadow" in message
    assert "Rollout Environment: live" in message
    assert "Rollout Stage: canary" in message
    assert "Rollout Recommended Action: promote_live_runtime_entries" in message
    assert "Live runtime shadow canary is healthy" in message
    assert "Live Rollout Approval" in message
    assert "Approval Required: True" in message
    assert "Approved By: ops" in message
    assert "Last Rollout Event: approval_granted (live_shadow)" in message


@pytest.mark.asyncio
async def test_cmd_status_uses_live_rollback_guidance():
    projection = make_projection(
        source=ProjectionSource.RUNTIME_PROJECTION,
        runtime_mode="runtime_primary",
    )
    object.__setattr__(projection, "runtime_is_healthy", False)

    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = FakeBot(
        projection,
        configured_deployment_profile="live_runtime_primary",
        trading_mode="live",
    )
    update = FakeUpdate()

    await notifier.cmd_status(update, None)

    message = update.message.calls[0]["text"]
    assert "Rollout Environment: live" in message
    assert "Rollout Stage: primary" in message
    assert "Rollout Recommended Action: rollback_to_live_legacy" in message
    assert "rollback to live_legacy" in message
