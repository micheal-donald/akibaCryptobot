from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import get_bot
from api.routes import trading
from operations.live_rollout_approval import (
    LiveRolloutApprovalRecord,
    LiveRolloutApprovalSnapshot,
    LiveRolloutAuditEvent,
)


class FakeTradingBot:
    def __init__(self):
        approval = LiveRolloutApprovalRecord(
            approval_id="approval-1",
            target_profile="live_shadow",
            actor="operator",
            reason="canary approved",
        )
        self.snapshot = LiveRolloutApprovalSnapshot(
            target_profile="live_shadow",
            approval_required=True,
            current_approval=approval,
            latest_approval=approval,
            latest_event=LiveRolloutAuditEvent(
                event_id="event-1",
                event_type="approval_granted",
                target_profile="live_shadow",
                actor="operator",
                reason="canary approved",
            ),
        )

    async def get_live_rollout_approval_status(self):
        return self.snapshot

    async def approve_live_rollout(
        self,
        *,
        actor: str,
        reason: str,
        target_profile: str,
        expires_in_hours: int | None = None,
    ):
        del expires_in_hours
        return LiveRolloutApprovalRecord(
            approval_id="approval-2",
            target_profile=target_profile,
            actor=actor,
            reason=reason,
        )

    async def record_live_rollback(
        self,
        *,
        actor: str,
        reason: str,
        target_profile: str | None = None,
    ):
        return (
            LiveRolloutApprovalRecord(
                approval_id="approval-2",
                target_profile=target_profile or "live_shadow",
                actor="operator",
                reason="canary approved",
                revoked_at=datetime.now(),
                revoked_by=actor,
                revocation_reason=reason,
            ),
        )


def build_client(bot) -> TestClient:
    app = FastAPI()
    app.include_router(trading.router, prefix="/api")
    app.dependency_overrides[get_bot] = lambda: bot
    return TestClient(app)


def test_get_live_rollout_approval_status():
    client = build_client(FakeTradingBot())

    response = client.get("/api/trading/live/approval")

    assert response.status_code == 200
    payload = response.json()
    assert payload["approval_required"] is True
    assert payload["current_approval"]["target_profile"] == "live_shadow"
    assert payload["latest_event"]["event_type"] == "approval_granted"


def test_post_live_approval_requires_operator_token(monkeypatch):
    monkeypatch.delenv("AKIBA_OPERATOR_TOKEN", raising=False)
    client = build_client(FakeTradingBot())

    response = client.post(
        "/api/trading/live/approve",
        json={
            "target_profile": "live_shadow",
            "actor": "ops",
            "reason": "approve canary",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "operator_token_not_configured"


def test_post_live_approval_returns_snapshot(monkeypatch):
    monkeypatch.setenv("AKIBA_OPERATOR_TOKEN", "secret")
    client = build_client(FakeTradingBot())

    response = client.post(
        "/api/trading/live/approve",
        json={
            "target_profile": "live_runtime_entries",
            "actor": "ops",
            "reason": "approve live canary",
            "expires_in_hours": 8,
        },
        headers={"X-Operator-Token": "secret"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["approval"]["target_profile"] == "live_runtime_entries"
    assert payload["approval"]["actor"] == "ops"
    assert payload["status"]["approval_required"] is True


def test_post_live_rollback_returns_revoked_approvals(monkeypatch):
    monkeypatch.setenv("AKIBA_OPERATOR_TOKEN", "secret")
    client = build_client(FakeTradingBot())

    response = client.post(
        "/api/trading/live/rollback",
        json={
            "actor": "ops",
            "reason": "rollback after alert",
            "target_profile": "live_shadow",
        },
        headers={"X-Operator-Token": "secret"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["revoked_approvals"][0]["status"] == "revoked"
