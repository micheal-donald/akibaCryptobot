from datetime import datetime, timezone

import pytest
from fastapi import WebSocketDisconnect

from api.dependencies import bot_state
from api.routes.status import get_status
from api.routes.trading import get_active_trades
from api.websockets import ws_status
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


class FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        self.sent.append(payload)
        raise WebSocketDisconnect(code=1000)


class FakePerformanceTracker:
    async def get_metrics_summary(self, trade_store=None):
        del trade_store
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


def make_legacy_trade():
    return {
        "symbol": "ETH/USDT",
        "side": "buy",
        "amount": 1.5,
        "entry_price": 3200.0,
    }


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


def make_projection(runtime_mode: str, *, runtime_is_healthy: bool = True):
    source_map = {
        "legacy": ProjectionSource.LEGACY,
        "shadow": ProjectionSource.SHADOW,
        "runtime_entries": ProjectionSource.RUNTIME_PROJECTION,
        "runtime_primary": ProjectionSource.RUNTIME_PROJECTION,
    }
    source = source_map[runtime_mode]
    owns_entries = runtime_mode in {"runtime_entries", "runtime_primary"}
    owns_exits = runtime_mode == "runtime_primary"
    positions = (make_position(),) if runtime_mode != "legacy" else ()
    active_position_count = len(positions)
    open_order_count = 2 if runtime_mode != "legacy" else 0
    pending_exit_count = 1 if runtime_mode == "runtime_primary" else 0

    return RuntimeProjection(
        source=source,
        runtime_mode=runtime_mode,
        runtime_enabled=runtime_mode != "legacy",
        runtime_is_running=runtime_mode != "legacy",
        runtime_is_healthy=runtime_is_healthy,
        runtime_owns_entries=owns_entries,
        runtime_owns_exits=owns_exits,
        active_position_count=active_position_count,
        protected_position_count=0,
        pending_exit_count=pending_exit_count,
        open_order_count=open_order_count,
        rejected_order_count=0,
        positions=positions,
        orders=(),
    )


class FakeBot:
    def __init__(self, projection: RuntimeProjection):
        self._projection = projection
        self.trade_store = object()
        self.performance_tracker = FakePerformanceTracker()
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.config = {"exchange": {"sandbox": True}}
        self.strategy_manager = type(
            "StrategyManager", (), {"active_strategy": "ensemble"}
        )()

    def get_status_info(self):
        return {
            "active_trades": 3,
            "daily_pnl": 12.34,
            "uptime": "0:10:00",
            "is_running": True,
            "runtime_mode": self._projection.runtime_mode,
            "runtime_is_running": self._projection.runtime_is_running,
            "runtime_owns_entries": self._projection.runtime_owns_entries,
            "runtime_owns_exits": self._projection.runtime_owns_exits,
        }

    def get_runtime_projection(self) -> RuntimeProjection:
        return self._projection

    def get_active_trades(self):
        return {"ETH/USDT": make_legacy_trade()}


async def gather_surface_outputs(bot: FakeBot, monkeypatch):
    status_payload = await get_status(bot=bot)
    trades_payload = await get_active_trades(bot=bot)

    websocket = FakeWebSocket()
    monkeypatch.setitem(bot_state, "bot", bot)
    await ws_status(websocket)

    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = bot
    status_update = FakeUpdate()
    trades_update = FakeUpdate()
    performance_update = FakeUpdate()
    metrics_update = FakeUpdate()

    await notifier.cmd_status(status_update, None)
    await notifier.cmd_trades(trades_update, None)
    await notifier.cmd_performance(performance_update, None)
    await notifier.cmd_metrics(metrics_update, None)

    return {
        "status": status_payload,
        "trades": trades_payload,
        "websocket": websocket.sent[0]["data"],
        "telegram_status": status_update.message.calls[0]["text"],
        "telegram_trades": trades_update.message.calls[0]["text"],
        "telegram_performance": performance_update.message.calls[0]["text"],
        "telegram_metrics": metrics_update.message.calls[0]["text"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime_mode", "expected_source", "expected_health", "expected_action"),
    [
        ("legacy", "legacy", "legacy", "stay_legacy"),
        ("shadow", "shadow", "shadow_ready", "continue_shadow"),
        (
            "runtime_entries",
            "runtime_projection",
            "promotion_ready",
            "promote_runtime_primary",
        ),
        (
            "runtime_primary",
            "runtime_projection",
            "promotion_ready",
            "stay_runtime_primary",
        ),
    ],
)
async def test_phase8_rollout_surfaces_end_to_end(
    runtime_mode, expected_source, expected_health, expected_action, monkeypatch
):
    bot = FakeBot(make_projection(runtime_mode))

    outputs = await gather_surface_outputs(bot, monkeypatch)

    status_payload = outputs["status"]
    trades_payload = outputs["trades"]
    websocket_payload = outputs["websocket"]

    assert status_payload["runtime_mode"] == runtime_mode
    assert status_payload["state_source"] == expected_source
    assert status_payload["rollout_health"] == expected_health
    assert status_payload["rollout_recommended_action"] == expected_action
    assert status_payload["runtime_is_healthy"] is True

    assert websocket_payload["runtime_mode"] == runtime_mode
    assert websocket_payload["state_source"] == expected_source
    assert websocket_payload["rollout_health"] == expected_health
    assert websocket_payload["rollout_recommended_action"] == expected_action

    assert outputs["telegram_status"].startswith("Bot Status: running")
    expected_active_trades = (
        "Active Trades: 1"
        if runtime_mode in {"runtime_entries", "runtime_primary"}
        else "Active Trades: 3"
    )
    assert expected_active_trades in outputs["telegram_status"]
    assert f"Runtime Mode: {runtime_mode}" in outputs["telegram_status"]
    assert f"State Source: {expected_source}" in outputs["telegram_status"]
    assert f"Rollout Health: {expected_health}" in outputs["telegram_status"]
    assert (
        f"Rollout Recommended Action: {expected_action}" in outputs["telegram_status"]
    )

    if runtime_mode == "legacy":
        assert trades_payload["source"] == "legacy"
        assert trades_payload["runtime_mode"] == "legacy"
        assert trades_payload["count"] == 1
        assert "ETH/USDT: BUY 1.5 @ $3200.000000" in outputs["telegram_trades"]
        assert "Runtime Mode: legacy" not in outputs["telegram_trades"]
        assert "Rollout Health: legacy" not in outputs["telegram_trades"]
        assert "Open Positions: 7" in outputs["telegram_performance"]
        assert "Open Positions: 7" in outputs["telegram_metrics"]
    elif runtime_mode == "shadow":
        assert trades_payload["source"] == "legacy"
        assert trades_payload["runtime_mode"] == "shadow"
        assert trades_payload["count"] == 1
        assert trades_payload["shadow_projected_count"] == 1
        assert "ETH/USDT: BUY 1.5 @ $3200.000000" in outputs["telegram_trades"]
        assert "Shadow Projected Positions: 1" in outputs["telegram_trades"]
        assert "Rollout Health: shadow_ready" in outputs["telegram_trades"]
        assert (
            "Rollout Recommended Action: continue_shadow" in outputs["telegram_trades"]
        )
        assert "Open Positions: 7" in outputs["telegram_performance"]
        assert "Open Positions: 7" in outputs["telegram_metrics"]
    else:
        assert trades_payload["source"] == "runtime_projection"
        assert trades_payload["runtime_mode"] == runtime_mode
        assert trades_payload["count"] == 1
        assert trades_payload["trades"][0]["source"] == "runtime_projection"
        assert "BTC/USDT: BUY 0.25 @ $65000.000000 [open]" in outputs["telegram_trades"]
        assert "Runtime Mode: " + runtime_mode in outputs["telegram_trades"]
        assert f"Rollout Health: {expected_health}" in outputs["telegram_trades"]
        assert (
            f"Rollout Recommended Action: {expected_action}"
            in outputs["telegram_trades"]
        )
        assert "Open Positions: 1" in outputs["telegram_performance"]
        assert "Open Positions: 1" in outputs["telegram_metrics"]

    assert f"Runtime Mode: {runtime_mode}" in outputs["telegram_performance"]
    assert f"Rollout Health: {expected_health}" in outputs["telegram_performance"]
    assert f"Runtime Mode: {runtime_mode}" in outputs["telegram_metrics"]
    assert f"Rollout Health: {expected_health}" in outputs["telegram_metrics"]


@pytest.mark.asyncio
async def test_phase8_rollout_rolls_back_to_legacy_after_unhealthy_promotion(
    monkeypatch,
):
    healthy_bot = FakeBot(make_projection("runtime_primary"))
    unhealthy_projection = make_projection("runtime_primary", runtime_is_healthy=False)
    unhealthy_bot = FakeBot(unhealthy_projection)
    legacy_bot = FakeBot(make_projection("legacy"))

    healthy_outputs = await gather_surface_outputs(healthy_bot, monkeypatch)
    assert healthy_outputs["status"]["rollout_health"] == "promotion_ready"
    assert healthy_outputs["status"]["rollout_recommended_action"] == (
        "stay_runtime_primary"
    )
    assert "promotion_ready" in healthy_outputs["telegram_status"]

    unhealthy_outputs = await gather_surface_outputs(unhealthy_bot, monkeypatch)
    assert unhealthy_outputs["status"]["rollout_health"] == "rollback_recommended"
    assert unhealthy_outputs["status"]["rollout_recommended_action"] == (
        "rollback_to_legacy"
    )
    assert "rollback to legacy is recommended" in unhealthy_outputs["telegram_status"]
    assert "rollback to legacy is recommended" in unhealthy_outputs["telegram_trades"]
    assert (
        "rollback to legacy is recommended" in unhealthy_outputs["telegram_performance"]
    )
    assert "rollback to legacy is recommended" in unhealthy_outputs["telegram_metrics"]

    legacy_outputs = await gather_surface_outputs(legacy_bot, monkeypatch)
    assert legacy_outputs["status"]["runtime_mode"] == "legacy"
    assert legacy_outputs["status"]["state_source"] == "legacy"
    assert legacy_outputs["status"]["rollout_health"] == "legacy"
    assert legacy_outputs["status"]["rollout_recommended_action"] == "stay_legacy"
    assert legacy_outputs["trades"]["source"] == "legacy"
    assert "Legacy loop remains authoritative." in legacy_outputs["telegram_status"]
    assert "Legacy loop remains authoritative." not in legacy_outputs["telegram_trades"]
    assert (
        "Legacy loop remains authoritative." in legacy_outputs["telegram_performance"]
    )
    assert "Legacy loop remains authoritative." in legacy_outputs["telegram_metrics"]
