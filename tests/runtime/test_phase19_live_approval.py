from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from operations.live_rollout_approval import (
    LIVE_ROLLOUT_AUDIT_APPROVAL_GRANTED,
    LIVE_ROLLOUT_AUDIT_ROLLBACK_RECORDED,
)
from persistence.trade_store import TradeStore
from runtime.config import RuntimeConfig
from runtime.mode import DeploymentProfile


@pytest.mark.asyncio
async def test_phase19_live_approval_persists_and_exposes_active_record(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    await store.initialize()

    approval = await store.create_live_rollout_approval(
        target_profile="live_shadow",
        actor="pm",
        reason="canary window opened",
        expires_in_hours=6,
    )

    active = await store.get_active_live_rollout_approval("live_shadow")
    latest_event = await store.get_latest_live_rollout_audit_event()

    assert active is not None
    assert active.approval_id == approval.approval_id
    assert active.is_active() is True
    assert latest_event is not None
    assert latest_event.event_type == LIVE_ROLLOUT_AUDIT_APPROVAL_GRANTED


@pytest.mark.asyncio
async def test_phase19_live_runtime_profile_requires_operator_approval(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    await store.initialize()

    config = RuntimeConfig.from_mapping(
        {
            "exchange": {"sandbox": False},
            "runtime": {
                "deployment_profile": "live_runtime_entries",
                "allow_live_runtime": True,
            },
        }
    )

    config.validate_startup(exchange_sandbox=False)

    assert (
        await store.get_active_live_rollout_approval(
            config.deployment_profile.value
        )
        is None
    )


@pytest.mark.asyncio
async def test_phase19_live_rollback_revokes_active_approval_and_records_audit(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    await store.initialize()
    await store.create_live_rollout_approval(
        target_profile=DeploymentProfile.LIVE_RUNTIME_PRIMARY.value,
        actor="ops",
        reason="promotion approved",
        expires_in_hours=12,
    )

    revoked = await store.revoke_live_rollout_approvals(
        actor="ops",
        reason="rollback requested after canary drift",
        target_profile=DeploymentProfile.LIVE_RUNTIME_PRIMARY.value,
    )
    active = await store.get_active_live_rollout_approval(
        DeploymentProfile.LIVE_RUNTIME_PRIMARY.value
    )
    latest_event = await store.get_latest_live_rollout_audit_event()

    assert len(revoked) == 1
    assert revoked[0].status() == "revoked"
    assert active is None
    assert latest_event is not None
    assert latest_event.event_type == LIVE_ROLLOUT_AUDIT_ROLLBACK_RECORDED


@pytest.mark.asyncio
async def test_phase19_expired_live_approval_is_not_returned_as_active(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    await store.initialize()
    approval = await store.create_live_rollout_approval(
        target_profile="live_shadow",
        actor="ops",
        reason="canary window opened",
        expires_in_hours=1,
    )

    expired_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await store._db.execute(
        "UPDATE live_rollout_approvals SET expires_at = ? WHERE approval_id = ?",
        (int(expired_at.timestamp()), approval.approval_id),
    )
    await store._db.commit()

    active = await store.get_active_live_rollout_approval("live_shadow")
    latest = await store.get_latest_live_rollout_approval("live_shadow")

    assert active is None
    assert latest is not None
    assert latest.status(now=datetime.now(timezone.utc)) == "expired"


@pytest.mark.asyncio
async def test_phase19_live_approval_rejects_non_positive_expiry(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    await store.initialize()

    with pytest.raises(ValueError, match="expires_in_hours_must_be_positive"):
        await store.create_live_rollout_approval(
            target_profile="live_shadow",
            actor="ops",
            reason="invalid expiry",
            expires_in_hours=0,
        )
