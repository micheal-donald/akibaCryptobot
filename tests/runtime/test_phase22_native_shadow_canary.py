from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from api.dependencies import bot_state
from api.routes.status import get_status
from api.websockets import ws_status
from operations.training_jobs import TrainingJobSnapshot
from ports.native_hot_path import NativeHotPathPorts
from runtime.ipc_protocol import IPCEnvelope, IPCPayloadKind
from runtime.native_bridge import (
    NativeShadowCanaryBridge,
    ingest_native_shadow_snapshot,
)
from runtime.projection import build_runtime_projection
from runtime.view_models import RuntimeProjection

NOW = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)
STALE_AT = NOW - timedelta(days=1)


class _ProjectionSource:
    def get_all_position_states(self) -> tuple[object, ...]:
        return ()

    def get_all_order_states(self) -> tuple[object, ...]:
        return ()


class FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        self.sent.append(payload)
        raise WebSocketDisconnect(code=1000)


class FakeCanaryBot:
    def __init__(self, projection: RuntimeProjection, *, native_shadow_snapshot=None):
        self._projection = projection
        self.runtime_app = SimpleNamespace(
            native_hot_path_ports=NativeHotPathPorts(
                market_data=object(),
                execution=object(),
            ),
            native_shadow_snapshot=native_shadow_snapshot,
        )
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.strategy_manager = SimpleNamespace(active_strategy="ensemble")
        self.config = {"exchange": {"sandbox": True}}

    def get_status_info(self):
        return {
            "active_trades": 0,
            "daily_pnl": 0.0,
            "uptime": "0:00:10",
            "is_running": True,
            "trading_paused": False,
            "trading_mode": "paper",
            "runtime_mode": self._projection.runtime_mode,
            "runtime_is_running": self._projection.runtime_is_running,
            "runtime_owns_entries": self._projection.runtime_owns_entries,
            "runtime_owns_exits": self._projection.runtime_owns_exits,
        }

    def get_runtime_visibility_info(self):
        return {
            "configured_deployment_profile": "paper_runtime_primary",
            "effective_runtime_mode": self._projection.runtime_mode,
            "effective_runtime_enabled": self._projection.runtime_enabled,
            "effective_runtime_owns_entries": self._projection.runtime_owns_entries,
            "effective_runtime_owns_exits": self._projection.runtime_owns_exits,
            "runtime_mode_source": "deployment_profile",
        }

    def get_runtime_projection(self) -> RuntimeProjection:
        return self._projection

    def get_runtime_telemetry_info(self):
        return {
            "total_events": 1,
            "event_counts": {},
            "metric_counts": {},
            "queue_depths": {},
            "last_seen_at": NOW.isoformat(),
            "latency": {"event_age_ms": {}, "exchange_age_ms": {}, "queue_lag_ms": {}},
        }

    async def get_training_job_status(self):
        return TrainingJobSnapshot(
            current_job=None,
            last_job=None,
            can_start_training=True,
            active_job_count=0,
        )

    def get_operational_alert_state(self):
        return {
            "operational_alerts": [],
            "halt_state": "none",
            "entries_blocked_reason": None,
        }


@pytest.fixture
def runtime_projection() -> RuntimeProjection:
    return build_runtime_projection(
        runtime_mode="runtime_primary",
        runtime_is_running=True,
        position_source=_ProjectionSource(),
        execution_source=_ProjectionSource(),
    )


@pytest.mark.asyncio
async def test_native_shadow_canary_bridge_ingests_snapshot_into_runtime_app() -> None:
    runtime_app = SimpleNamespace(native_shadow_snapshot=None)
    snapshot = ingest_native_shadow_snapshot(
        IPCEnvelope.create(
            IPCPayloadKind.MARKET_DATA,
            {
                "runtime_mode": "runtime_primary",
                "runtime_is_running": True,
                "runtime_owns_entries": True,
                "runtime_owns_exits": True,
                "active_position_count": 2,
                "open_order_count": 1,
                "captured_at": NOW.isoformat(),
            },
        ),
        runtime_app=runtime_app,
    )

    assert runtime_app.native_shadow_snapshot is snapshot
    assert snapshot.captured_at == NOW
    assert snapshot.age_seconds(now=NOW) == 0.0


@pytest.mark.asyncio
async def test_status_route_surfaces_native_shadow_missing_snapshot(
    runtime_projection: RuntimeProjection,
) -> None:
    bot = FakeCanaryBot(runtime_projection)

    payload = await get_status(bot=bot)

    report = payload["native_shadow_report"]
    assert report["native_shadow_status"] == "awaiting_native_snapshot"
    assert report["native_shadow_is_match"] is False
    assert report["native_shadow_mismatches"] == ("native_snapshot_missing",)
    assert report["native_shadow_recommended_action"] == "wait_for_native_snapshot"


@pytest.mark.asyncio
async def test_ws_status_surfaces_native_shadow_stale_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    runtime_projection: RuntimeProjection,
) -> None:
    bridge = NativeShadowCanaryBridge()
    runtime_app = SimpleNamespace(native_hot_path_ports=NativeHotPathPorts(market_data=object(), execution=object()))
    runtime_app.native_shadow_snapshot = bridge.ingest_snapshot(
        IPCEnvelope.create(
            IPCPayloadKind.MARKET_DATA,
            {
                "runtime_mode": "runtime_primary",
                "runtime_is_running": True,
                "runtime_owns_entries": True,
                "runtime_owns_exits": True,
                "active_position_count": 2,
                "open_order_count": 1,
                "captured_at": STALE_AT.isoformat(),
            },
        ),
        runtime_app=None,
    )
    bot = FakeCanaryBot(runtime_projection, native_shadow_snapshot=runtime_app.native_shadow_snapshot)
    monkeypatch.setitem(bot_state, "bot", bot)
    websocket = FakeWebSocket()

    await ws_status(websocket)

    assert websocket.accepted is True
    payload = websocket.sent[0]["data"]
    report = payload["native_shadow_report"]
    assert report["native_shadow_status"] == "stale_native_snapshot"
    assert report["native_shadow_is_match"] is False
    assert report["native_shadow_mismatches"] == ("native_snapshot_stale",)
    assert report["native_shadow_recommended_action"] == "refresh_native_snapshot"
    assert report["native_shadow_last_seen_at"] == STALE_AT.isoformat()


@pytest.mark.asyncio
async def test_status_route_surfaces_native_shadow_divergence(
    runtime_projection: RuntimeProjection,
) -> None:
    runtime_app = SimpleNamespace(native_hot_path_ports=NativeHotPathPorts(market_data=object(), execution=object()))
    runtime_app.native_shadow_snapshot = ingest_native_shadow_snapshot(
        IPCEnvelope.create(
            IPCPayloadKind.MARKET_DATA,
            {
                "runtime_mode": "shadow",
                "runtime_is_running": False,
                "runtime_owns_entries": False,
                "runtime_owns_exits": False,
                "active_position_count": 1,
                "open_order_count": 1,
                "captured_at": NOW.isoformat(),
            },
        ),
        runtime_app=None,
    )
    bot = FakeCanaryBot(runtime_projection, native_shadow_snapshot=runtime_app.native_shadow_snapshot)

    payload = await get_status(bot=bot)

    report = payload["native_shadow_report"]
    assert report["native_shadow_status"] == "divergent"
    assert report["native_shadow_is_match"] is False
    assert set(report["native_shadow_mismatches"]) >= {
        "runtime_mode",
        "runtime_is_running",
        "runtime_owns_entries",
        "runtime_owns_exits",
        "active_position_count",
        "open_order_count",
    }
    assert report["native_shadow_recommended_action"] == "investigate_native_divergence"
