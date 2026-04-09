"""Live runtime approval and audit state models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from runtime.mode import DeploymentProfile

LIVE_ROLLOUT_APPROVAL_ACTIVE = "active"
LIVE_ROLLOUT_APPROVAL_EXPIRED = "expired"
LIVE_ROLLOUT_APPROVAL_REVOKED = "revoked"

LIVE_ROLLOUT_AUDIT_APPROVAL_GRANTED = "approval_granted"
LIVE_ROLLOUT_AUDIT_STARTUP_VALIDATED = "startup_validated"
LIVE_ROLLOUT_AUDIT_STARTUP_REJECTED = "startup_rejected"
LIVE_ROLLOUT_AUDIT_ROLLBACK_RECORDED = "rollback_recorded"

LIVE_ROLLOUT_AUDIT_EVENT_TYPES = {
    LIVE_ROLLOUT_AUDIT_APPROVAL_GRANTED,
    LIVE_ROLLOUT_AUDIT_STARTUP_VALIDATED,
    LIVE_ROLLOUT_AUDIT_STARTUP_REJECTED,
    LIVE_ROLLOUT_AUDIT_ROLLBACK_RECORDED,
}


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _normalize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    return {str(key): value for key, value in metadata.items()}


@dataclass(slots=True, frozen=True)
class LiveRolloutApprovalRecord:
    """Durable operator approval for a reviewed live runtime profile."""

    approval_id: str = field(default_factory=lambda: uuid4().hex)
    target_profile: str = DeploymentProfile.LIVE_SHADOW.value
    actor: str = "system"
    reason: str = ""
    approved_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    revocation_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "approval_id", str(self.approval_id))
        profile = DeploymentProfile.parse(self.target_profile)
        if not profile.requires_live_runtime_approval:
            raise ValueError(
                f"live rollout approval is not required for {profile.value!r}"
            )
        object.__setattr__(self, "target_profile", profile.value)
        object.__setattr__(self, "actor", str(self.actor).strip())
        object.__setattr__(self, "reason", str(self.reason).strip())
        if not self.actor:
            raise ValueError("approval actor must not be empty")
        if not self.reason:
            raise ValueError("approval reason must not be empty")
        object.__setattr__(self, "approved_at", _normalize_datetime(self.approved_at))
        object.__setattr__(self, "expires_at", _normalize_datetime(self.expires_at))
        object.__setattr__(self, "revoked_at", _normalize_datetime(self.revoked_at))
        if self.revoked_by is not None:
            object.__setattr__(self, "revoked_by", str(self.revoked_by).strip() or None)
        if self.revocation_reason is not None:
            object.__setattr__(
                self,
                "revocation_reason",
                str(self.revocation_reason).strip() or None,
            )

    def status(self, *, now: datetime | None = None) -> str:
        if self.revoked_at is not None:
            return LIVE_ROLLOUT_APPROVAL_REVOKED
        if self.expires_at is not None:
            current_time = _normalize_datetime(now) or datetime.now(timezone.utc)
            if self.expires_at <= current_time:
                return LIVE_ROLLOUT_APPROVAL_EXPIRED
        return LIVE_ROLLOUT_APPROVAL_ACTIVE

    def is_active(self, *, now: datetime | None = None) -> bool:
        return self.status(now=now) == LIVE_ROLLOUT_APPROVAL_ACTIVE

    def to_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "target_profile": self.target_profile,
            "actor": self.actor,
            "reason": self.reason,
            "approved_at": (
                self.approved_at.isoformat() if self.approved_at is not None else None
            ),
            "expires_at": (
                self.expires_at.isoformat() if self.expires_at is not None else None
            ),
            "revoked_at": (
                self.revoked_at.isoformat() if self.revoked_at is not None else None
            ),
            "revoked_by": self.revoked_by,
            "revocation_reason": self.revocation_reason,
            "status": self.status(now=now),
            "is_active": self.is_active(now=now),
        }


@dataclass(slots=True, frozen=True)
class LiveRolloutAuditEvent:
    """Durable audit event for live rollout transitions."""

    event_id: str = field(default_factory=lambda: uuid4().hex)
    event_type: str = LIVE_ROLLOUT_AUDIT_APPROVAL_GRANTED
    target_profile: str = DeploymentProfile.LIVE_SHADOW.value
    actor: str = "system"
    reason: str = ""
    created_at: datetime | None = None
    approval_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", str(self.event_id))
        if self.event_type not in LIVE_ROLLOUT_AUDIT_EVENT_TYPES:
            raise ValueError(f"invalid live rollout audit event type: {self.event_type}")
        profile = DeploymentProfile.parse(self.target_profile)
        if not profile.is_live:
            raise ValueError("live rollout audit events require a live deployment profile")
        object.__setattr__(self, "target_profile", profile.value)
        object.__setattr__(self, "actor", str(self.actor).strip())
        object.__setattr__(self, "reason", str(self.reason).strip())
        if not self.actor:
            raise ValueError("audit actor must not be empty")
        if not self.reason:
            raise ValueError("audit reason must not be empty")
        object.__setattr__(self, "created_at", _normalize_datetime(self.created_at))
        object.__setattr__(
            self,
            "approval_id",
            None if self.approval_id is None else str(self.approval_id),
        )
        object.__setattr__(self, "metadata", _normalize_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "target_profile": self.target_profile,
            "actor": self.actor,
            "reason": self.reason,
            "created_at": (
                self.created_at.isoformat() if self.created_at is not None else None
            ),
            "approval_id": self.approval_id,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True, frozen=True)
class LiveRolloutApprovalSnapshot:
    """Operator-facing summary of the current live approval state."""

    target_profile: str | None
    approval_required: bool
    current_approval: LiveRolloutApprovalRecord | None = None
    latest_approval: LiveRolloutApprovalRecord | None = None
    latest_event: LiveRolloutAuditEvent | None = None

    def to_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        return {
            "target_profile": self.target_profile,
            "approval_required": self.approval_required,
            "current_approval": (
                self.current_approval.to_dict(now=now)
                if self.current_approval is not None
                else None
            ),
            "latest_approval": (
                self.latest_approval.to_dict(now=now)
                if self.latest_approval is not None
                else None
            ),
            "latest_event": (
                self.latest_event.to_dict() if self.latest_event is not None else None
            ),
        }
