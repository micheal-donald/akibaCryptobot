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


NOW = datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)


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
        DeploymentProfile.LIVE_LEGACY: "legacy",
        DeploymentProfile.LIVE_SHADOW: "shadow",
        DeploymentProfile.LIVE_RUNTIME_ENTRIES: "runtime_entries",
        DeploymentProfile.LIVE_RUNTIME_PRIMARY: "runtime_primary",
    }
    runtime_mode = runtime_mode_map[profile]
    source = (
        ProjectionSource.LEGACY
        if profile is DeploymentProfile.LIVE_LEGACY
        else (
            ProjectionSource.SHADOW
            if profile is DeploymentProfile.LIVE_SHADOW
            else ProjectionSource.RUNTIME_PROJECTION
        )
    )
    positions = (
        (make_position(),)
        if profile
        in {
            DeploymentProfile.LIVE_RUNTIME_ENTRIES,
            DeploymentProfile.LIVE_RUNTIME_PRIMARY,
        }
        else ()
    )
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
        pending_exit_count=1 if runtime_mode == "runtime_primary" else 0,
        open_order_count=2 if runtime_mode != "legacy" else 0,
        rejected_order_count=0,
        positions=positions,
        orders=(),
    )


class FakeLiveBot:
    def __init__(self, profile: DeploymentProfile, projection: RuntimeProjection):
        self._profile = profile
        self._projection = projection
        self.config = {"exchange": {"sandbox": False}}
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.strategy_manager = SimpleNamespace(active_strategy="ensemble")
        self.trade_store = object()
        self.performance_tracker = SimpleNamespace(
            get_metrics_summary=self._get_metrics_summary
        )

    async def _get_metrics_summary(self, trade_store=None):
        del trade_store
        return {
            "initial_balance": 1000.0,
            "current_balance": 1015.0,
            "total_pnl": 15.0,
            "total_return_percent": 1.5,
            "total_trades": 2,
            "winning_trades": 2,
            "losing_trades": 0,
            "win_rate": 100.0,
            "average_win": 7.5,
            "average_loss": 0.0,
            "win_loss_ratio": 0.0,
            "sharpe_ratio": 1.4,
            "sortino_ratio": 1.8,
            "profit_factor": 2.1,
            "expectancy": 7.5,
            "max_drawdown_percent": 0.5,
            "current_drawdown_percent": 0.1,
            "open_trades_count": 4,
        }

    def get_status_info(self):
        return {
            "active_trades": 3,
            "daily_pnl": 15.0,
            "uptime": "0:10:00",
            "is_running": True,
            "trading_mode": "live",
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
        return {
            "ETH/USDT": {
                "symbol": "ETH/USDT",
                "side": "buy",
                "amount": 1.5,
                "entry_price": 3200.0,
            }
        }


async def gather_outputs(bot, monkeypatch):
    status_payload = await get_status(bot=bot)
    websocket = FakeWebSocket()
    monkeypatch.setitem(bot_state, "bot", bot)
    await ws_status(websocket)

    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = bot
    status_update = FakeUpdate()

    await notifier.cmd_status(status_update, None)

    return {
        "status": status_payload,
        "websocket": websocket.sent[0]["data"],
        "telegram_status": status_update.message.calls[0]["text"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "expected_state_source", "expected_stage", "expected_action"),
    [
        (
            DeploymentProfile.LIVE_LEGACY,
            "legacy",
            "legacy",
            "stay_live_legacy",
        ),
        (
            DeploymentProfile.LIVE_SHADOW,
            "shadow",
            "canary",
            "promote_live_runtime_entries",
        ),
        (
            DeploymentProfile.LIVE_RUNTIME_ENTRIES,
            "runtime_projection",
            "canary",
            "promote_live_runtime_primary",
        ),
    ],
)
async def test_phase11_live_profiles_across_operator_surfaces(
    profile,
    expected_state_source,
    expected_stage,
    expected_action,
    monkeypatch,
):
    bot = FakeLiveBot(profile=profile, projection=make_projection(profile))

    outputs = await gather_outputs(bot, monkeypatch)

    for payload in (outputs["status"], outputs["websocket"]):
        assert payload["trading_mode"] == "live"
        assert payload["configured_deployment_profile"] == profile.value
        assert payload["state_source"] == expected_state_source
        assert payload["rollout_environment"] == "live"
        assert payload["rollout_stage"] == expected_stage
        assert payload["rollout_recommended_action"] == expected_action

    assert "Rollout Environment: live" in outputs["telegram_status"]
    assert f"Rollout Stage: {expected_stage}" in outputs["telegram_status"]
    assert (
        f"Rollout Recommended Action: {expected_action}" in outputs["telegram_status"]
    )


@pytest.mark.asyncio
async def test_phase11_live_startup_pending_reports_wait_guidance(monkeypatch):
    projection = make_projection(DeploymentProfile.LIVE_SHADOW)
    object.__setattr__(projection, "runtime_is_running", False)
    object.__setattr__(projection, "runtime_is_healthy", False)
    object.__setattr__(projection, "runtime_start_status", "starting")
    object.__setattr__(projection, "runtime_start_pending", True)
    bot = FakeLiveBot(profile=DeploymentProfile.LIVE_SHADOW, projection=projection)

    outputs = await gather_outputs(bot, monkeypatch)

    for payload in (outputs["status"], outputs["websocket"]):
        assert payload["rollout_environment"] == "live"
        assert payload["rollout_stage"] == "canary"
        assert payload["rollout_health"] == "startup_pending"
        assert payload["rollout_recommended_action"] == "wait_for_live_runtime"

    assert (
        "Rollout Recommended Action: wait_for_live_runtime"
        in outputs["telegram_status"]
    )
    assert "Live runtime startup is still in progress" in outputs["telegram_status"]


@pytest.mark.asyncio
async def test_phase11_live_primary_unhealthy_reports_live_rollback(monkeypatch):
    projection = make_projection(DeploymentProfile.LIVE_RUNTIME_PRIMARY)
    object.__setattr__(projection, "runtime_is_healthy", False)
    bot = FakeLiveBot(
        profile=DeploymentProfile.LIVE_RUNTIME_PRIMARY,
        projection=projection,
    )

    outputs = await gather_outputs(bot, monkeypatch)

    for payload in (outputs["status"], outputs["websocket"]):
        assert payload["rollout_environment"] == "live"
        assert payload["rollout_stage"] == "primary"
        assert payload["rollout_health"] == "rollback_recommended"
        assert payload["rollout_recommended_action"] == "rollback_to_live_legacy"

    assert (
        "Rollout Recommended Action: rollback_to_live_legacy"
        in outputs["telegram_status"]
    )
    assert "rollback to live_legacy" in outputs["telegram_status"]


@pytest.mark.asyncio
async def test_phase11_live_runtime_rejection_without_override():
    bot = AkibaCryptoBot.__new__(AkibaCryptoBot)
    bot.config = {
        "exchange": {"sandbox": False},
        "runtime": {"deployment_profile": "live_runtime_entries"},
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

    with pytest.raises(ValueError, match="live_runtime_entries"):
        await bot.start()

    assert bot.is_running is False
