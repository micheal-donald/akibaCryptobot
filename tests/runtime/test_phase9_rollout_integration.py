from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from api.dependencies import bot_state
from api.routes.status import get_status
from api.websockets import ws_status
from bot import AkibaCryptoBot
from notifications.telegram_bot import TelegramNotifier
from runtime.mode import DeploymentProfile
from runtime.view_models import (
    ProjectedPositionView,
    ProjectionSource,
    RuntimeProjection,
)


NOW = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)


class FakeMessage:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def reply_text(self, text, parse_mode=None):
        self.calls.append({"text": text, "parse_mode": parse_mode})


class FakeUpdate:
    def __init__(self) -> None:
        self.message = FakeMessage()


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict[str, object]] = []

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


def make_projection(profile: DeploymentProfile) -> RuntimeProjection:
    runtime_mode_map = {
        DeploymentProfile.PAPER_LEGACY: "legacy",
        DeploymentProfile.PAPER_SHADOW: "shadow",
        DeploymentProfile.PAPER_RUNTIME_ENTRIES: "runtime_entries",
        DeploymentProfile.PAPER_RUNTIME_PRIMARY: "runtime_primary",
        DeploymentProfile.LIVE_LEGACY: "legacy",
    }
    runtime_mode = runtime_mode_map[profile]
    source = (
        ProjectionSource.LEGACY
        if profile in {DeploymentProfile.PAPER_LEGACY, DeploymentProfile.LIVE_LEGACY}
        else (
            ProjectionSource.SHADOW
            if profile is DeploymentProfile.PAPER_SHADOW
            else ProjectionSource.RUNTIME_PROJECTION
        )
    )
    positions = (make_position(),) if runtime_mode != "legacy" else ()
    open_order_count = 2 if runtime_mode != "legacy" else 0
    pending_exit_count = 1 if runtime_mode == "runtime_primary" else 0

    return RuntimeProjection(
        source=source,
        runtime_mode=runtime_mode,
        runtime_enabled=runtime_mode != "legacy",
        runtime_is_running=runtime_mode != "legacy",
        runtime_is_healthy=True,
        runtime_owns_entries=runtime_mode in {"runtime_entries", "runtime_primary"},
        runtime_owns_exits=runtime_mode == "runtime_primary",
        active_position_count=len(positions),
        protected_position_count=0,
        pending_exit_count=pending_exit_count,
        open_order_count=open_order_count,
        rejected_order_count=0,
        positions=positions,
        orders=(),
    )


class FakeProfileBot:
    def __init__(
        self,
        *,
        profile: DeploymentProfile,
        projection: RuntimeProjection,
    ) -> None:
        self.config = {"exchange": {"sandbox": True}}
        self._projection = projection
        self._profile = profile
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.strategy_manager = SimpleNamespace(active_strategy="ensemble")
        self.trade_store = object()
        self.performance_tracker = FakePerformanceTracker()

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

    def get_runtime_visibility_info(self):
        return {
            "configured_deployment_profile": self._profile.value,
            "effective_runtime_mode": self._projection.runtime_mode,
            "effective_runtime_enabled": self._projection.runtime_enabled,
            "effective_runtime_owns_entries": self._projection.runtime_owns_entries,
            "effective_runtime_owns_exits": self._projection.runtime_owns_exits,
            "runtime_mode_source": "deployment_profile",
        }

    def get_active_trades(self):
        if self._projection.source is ProjectionSource.RUNTIME_PROJECTION:
            return {
                "BTC/USDT": {
                    "symbol": "BTC/USDT",
                    "side": "buy",
                    "amount": 0.25,
                    "entry_price": 65000.0,
                }
            }
        return {
            "ETH/USDT": {
                "symbol": "ETH/USDT",
                "side": "buy",
                "amount": 1.5,
                "entry_price": 3200.0,
            }
        }


async def gather_operator_surface_outputs(bot, monkeypatch):
    status_payload = await get_status(bot=bot)
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
        "websocket": websocket.sent[0]["data"],
        "telegram_status": status_update.message.calls[0]["text"],
        "telegram_trades": trades_update.message.calls[0]["text"],
        "telegram_performance": performance_update.message.calls[0]["text"],
        "telegram_metrics": metrics_update.message.calls[0]["text"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "expected_state_source", "expected_health", "expected_action"),
    [
        (DeploymentProfile.PAPER_LEGACY, "legacy", "legacy", "stay_legacy"),
        (DeploymentProfile.PAPER_SHADOW, "shadow", "shadow_ready", "continue_shadow"),
        (
            DeploymentProfile.PAPER_RUNTIME_ENTRIES,
            "runtime_projection",
            "promotion_ready",
            "promote_runtime_primary",
        ),
        (
            DeploymentProfile.PAPER_RUNTIME_PRIMARY,
            "runtime_projection",
            "promotion_ready",
            "stay_runtime_primary",
        ),
    ],
)
async def test_phase9_paper_profile_promotion_across_operator_surfaces(
    profile,
    expected_state_source,
    expected_health,
    expected_action,
    monkeypatch,
):
    bot = FakeProfileBot(profile=profile, projection=make_projection(profile))

    outputs = await gather_operator_surface_outputs(bot, monkeypatch)
    status_payload = outputs["status"]
    websocket_payload = outputs["websocket"]

    assert status_payload["configured_deployment_profile"] == profile.value
    assert (
        status_payload["effective_runtime_mode"]
        == bot.get_runtime_projection().runtime_mode
    )
    assert status_payload["state_source"] == expected_state_source
    assert status_payload["rollout_health"] == expected_health
    assert status_payload["rollout_recommended_action"] == expected_action

    assert websocket_payload["configured_deployment_profile"] == profile.value
    assert (
        websocket_payload["effective_runtime_mode"]
        == bot.get_runtime_projection().runtime_mode
    )
    assert websocket_payload["state_source"] == expected_state_source
    assert websocket_payload["rollout_health"] == expected_health
    assert websocket_payload["rollout_recommended_action"] == expected_action

    assert (
        f"Runtime Mode: {bot.get_runtime_projection().runtime_mode}"
        in outputs["telegram_status"]
    )
    assert f"State Source: {expected_state_source}" in outputs["telegram_status"]
    assert f"Rollout Health: {expected_health}" in outputs["telegram_status"]
    assert (
        f"Rollout Recommended Action: {expected_action}" in outputs["telegram_status"]
    )

    if profile in {
        DeploymentProfile.PAPER_RUNTIME_ENTRIES,
        DeploymentProfile.PAPER_RUNTIME_PRIMARY,
    }:
        assert "BTC/USDT: BUY 0.25 @ $65000.000000 [open]" in outputs["telegram_trades"]
        assert (
            "Runtime Mode: " + bot.get_runtime_projection().runtime_mode
            in outputs["telegram_trades"]
        )
        assert "Open Positions: 1" in outputs["telegram_performance"]
        assert "Open Positions: 1" in outputs["telegram_metrics"]
    elif profile is DeploymentProfile.PAPER_SHADOW:
        assert "ETH/USDT: BUY 1.5 @ $3200.000000" in outputs["telegram_trades"]
        assert "Shadow Projected Positions: 1" in outputs["telegram_trades"]
        assert "Open Positions: 7" in outputs["telegram_performance"]
        assert "Open Positions: 7" in outputs["telegram_metrics"]
    else:
        assert "ETH/USDT: BUY 1.5 @ $3200.000000" in outputs["telegram_trades"]
        assert "Runtime Mode: legacy" not in outputs["telegram_trades"]
        assert "Rollout Health: legacy" not in outputs["telegram_trades"]
        assert "Open Positions: 7" in outputs["telegram_performance"]
        assert "Open Positions: 7" in outputs["telegram_metrics"]


@pytest.mark.asyncio
async def test_phase9_rollback_to_paper_legacy_restores_legacy_operator_surfaces(
    monkeypatch,
):
    bot = FakeProfileBot(
        profile=DeploymentProfile.PAPER_RUNTIME_PRIMARY,
        projection=make_projection(DeploymentProfile.PAPER_RUNTIME_PRIMARY),
    )

    promoted_outputs = await gather_operator_surface_outputs(bot, monkeypatch)
    assert (
        promoted_outputs["status"]["configured_deployment_profile"]
        == "paper_runtime_primary"
    )
    assert promoted_outputs["status"]["rollout_recommended_action"] == (
        "stay_runtime_primary"
    )

    bot._profile = DeploymentProfile.PAPER_LEGACY
    bot._projection = make_projection(DeploymentProfile.PAPER_LEGACY)

    rolled_back_outputs = await gather_operator_surface_outputs(bot, monkeypatch)
    assert (
        rolled_back_outputs["status"]["configured_deployment_profile"] == "paper_legacy"
    )
    assert rolled_back_outputs["status"]["state_source"] == "legacy"
    assert rolled_back_outputs["status"]["rollout_health"] == "legacy"
    assert rolled_back_outputs["status"]["rollout_recommended_action"] == "stay_legacy"
    assert rolled_back_outputs["websocket"]["state_source"] == "legacy"
    assert rolled_back_outputs["websocket"]["rollout_health"] == "legacy"
    assert (
        "Legacy loop remains authoritative." in rolled_back_outputs["telegram_status"]
    )
    assert (
        "Legacy loop remains authoritative."
        in rolled_back_outputs["telegram_performance"]
    )
    assert (
        "Legacy loop remains authoritative." in rolled_back_outputs["telegram_metrics"]
    )


@pytest.mark.asyncio
async def test_phase9_live_runtime_rejection_fails_without_explicit_override():
    bot = AkibaCryptoBot.__new__(AkibaCryptoBot)
    bot.config = {
        "exchange": {"sandbox": False},
        "runtime": {"deployment_profile": "paper_runtime_primary"},
        "trading": {"pairs": ["BTC/USDT"], "timeframes": {"primary": "1m"}},
    }
    bot.logger = SimpleNamespace(info=lambda *args, **kwargs: None)
    bot.strategy_manager = SimpleNamespace(active_strategy="ensemble")
    bot.start_time = NOW
    bot.circuit_breaker_active = False
    bot.circuit_breaker_reason = ""
    bot.active_trades = {}
    bot.is_running = False
    bot._stop_event = SimpleNamespace(clear=lambda: None)
    bot._cleanup_future = None
    bot.trade_store = SimpleNamespace(
        initialize=lambda: None,
        load_open_trades=lambda: {},
        start_session=lambda **kwargs: None,
    )
    bot.position_manager = SimpleNamespace()
    bot.opportunity_scanner = SimpleNamespace()
    bot.performance_tracker = SimpleNamespace()

    with pytest.raises(ValueError, match="allow_live_runtime"):
        await bot.start()

    assert bot.is_running is False
