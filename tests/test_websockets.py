from datetime import datetime, timezone

import pytest
from fastapi import WebSocketDisconnect

from api.dependencies import bot_state
from api.websockets import ws_status
from runtime.view_models import (
    ProjectedPositionView,
    ProjectionSource,
    RuntimeProjection,
)


NOW = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)


class FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        self.sent.append(payload)
        raise WebSocketDisconnect(code=1000)


class FakeStatusBot:
    def __init__(self, projection=None, *, configured_deployment_profile=None):
        self._projection = projection
        self._configured_deployment_profile = configured_deployment_profile
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.strategy_manager = type(
            "StrategyManager", (), {"active_strategy": "ensemble"}
        )()

    def get_status_info(self):
        return {
            "active_trades": 3,
            "daily_pnl": 12.34,
            "uptime": "0:10:00",
            "is_running": True,
            "trading_mode": "paper",
        }

    def get_runtime_projection(self):
        return self._projection

    def get_runtime_visibility_info(self):
        if self._projection is None:
            return {
                "configured_deployment_profile": self._configured_deployment_profile,
                "effective_runtime_mode": "legacy",
                "effective_runtime_enabled": False,
                "effective_runtime_owns_entries": False,
                "effective_runtime_owns_exits": False,
                "runtime_mode_source": "legacy_default",
            }
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


def make_projection(
    *, source: ProjectionSource, runtime_mode: str
) -> RuntimeProjection:
    position = ProjectedPositionView(
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
    )
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
        rejected_order_count=1,
        positions=(position,),
        orders=(),
    )


@pytest.mark.asyncio
async def test_ws_status_uses_runtime_projection_when_available(monkeypatch):
    monkeypatch.setitem(
        bot_state,
        "bot",
        FakeStatusBot(
            make_projection(
                source=ProjectionSource.RUNTIME_PROJECTION,
                runtime_mode="runtime_primary",
            ),
            configured_deployment_profile="paper_runtime_primary",
        ),
    )
    websocket = FakeWebSocket()

    await ws_status(websocket)

    assert websocket.accepted is True
    assert len(websocket.sent) == 1
    payload = websocket.sent[0]
    assert payload["type"] == "status"
    data = payload["data"]
    assert data["active_trades"] == 1
    assert data["runtime_mode"] == "runtime_primary"
    assert data["configured_deployment_profile"] == "paper_runtime_primary"
    assert data["effective_runtime_mode"] == "runtime_primary"
    assert data["runtime_is_running"] is True
    assert data["runtime_owns_entries"] is True
    assert data["runtime_owns_exits"] is True
    assert data["runtime_is_healthy"] is True
    assert data["state_source"] == "runtime_projection"
    assert data["rollout_health"] == "promotion_ready"
    assert data["rollout_recommended_action"] == "stay_runtime_primary"
    assert "Runtime primary ownership is healthy" in data["rollout_summary"]
    assert data["projected_active_positions"] == 1
    assert data["projected_open_orders"] == 2
    assert data["projected_pending_exits"] == 1
    assert data["projected_rejected_orders"] == 1
    assert data["strategy"] == "ensemble"
    assert data["trading_mode"] == "paper"


@pytest.mark.asyncio
async def test_ws_status_keeps_legacy_state_when_projection_missing(monkeypatch):
    monkeypatch.setitem(bot_state, "bot", FakeStatusBot())
    websocket = FakeWebSocket()

    await ws_status(websocket)

    assert websocket.accepted is True
    assert len(websocket.sent) == 1
    payload = websocket.sent[0]
    assert payload["type"] == "status"
    data = payload["data"]
    assert data["active_trades"] == 3
    assert data["runtime_mode"] == "legacy"
    assert data["configured_deployment_profile"] is None
    assert data["effective_runtime_mode"] == "legacy"
    assert data["runtime_is_running"] is False
    assert data["runtime_owns_entries"] is False
    assert data["runtime_owns_exits"] is False
    assert data["runtime_is_healthy"] is True
    assert data["state_source"] == "legacy"
    assert data["rollout_health"] == "legacy"
    assert data["rollout_recommended_action"] == "stay_legacy"
    assert "Legacy loop remains authoritative" in data["rollout_summary"]
    assert data["projected_active_positions"] == 0
    assert data["projected_open_orders"] == 0
