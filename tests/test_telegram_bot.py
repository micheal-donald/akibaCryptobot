from datetime import datetime, timezone

import pytest

from notifications.telegram_bot import TelegramNotifier
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
    def __init__(self, projection: RuntimeProjection):
        self._projection = projection
        self.trade_store = object()
        self.performance_tracker = FakePerformanceTracker()

    def get_status_info(self):
        return {
            "active_trades": 3,
            "daily_pnl": 12.34,
            "uptime": "0:10:00",
        }

    def get_runtime_projection(self) -> RuntimeProjection:
        return self._projection

    def get_active_trades(self):
        return {
            "ETH/USDT": {
                "symbol": "ETH/USDT",
                "side": "buy",
                "amount": 1.5,
                "entry_price": 3200.0,
            }
        }


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
    assert "Runtime Healthy: yes" in message
    assert "Rollout Health: promotion_ready" in message
    assert "Rollout Recommended Action: stay_runtime_primary" in message
    assert "Rollout Summary: Runtime primary ownership is healthy" in message
    assert "Projected Open Orders: 2" in message
    assert "Projected Pending Exits: 1" in message


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
    assert "Rollout Health: promotion_ready" in message
    assert "Rollout Recommended Action: stay_runtime_primary" in message


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
    assert "Rollout Health: startup_pending" in message
    assert "Rollout Recommended Action: wait_for_runtime" in message
    assert "startup is still in progress" in message
