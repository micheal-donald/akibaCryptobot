"""Operational alert and monitoring state models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

ALERT_SEVERITY_INFO = "info"
ALERT_SEVERITY_WARNING = "warning"
ALERT_SEVERITY_CRITICAL = "critical"

ALERT_SEVERITIES = {
    ALERT_SEVERITY_INFO,
    ALERT_SEVERITY_WARNING,
    ALERT_SEVERITY_CRITICAL,
}

HALT_STATE_NONE = "none"
HALT_STATE_ENTRIES_BLOCKED = "entries_blocked"
HALT_STATE_RUNTIME_KILL_SWITCH = "runtime_kill_switch"
HALT_STATE_LEGACY_CIRCUIT_BREAKER = "legacy_circuit_breaker"

HALT_STATES = {
    HALT_STATE_NONE,
    HALT_STATE_ENTRIES_BLOCKED,
    HALT_STATE_RUNTIME_KILL_SWITCH,
    HALT_STATE_LEGACY_CIRCUIT_BREAKER,
}


def _coerce_int(*values: Any, default: int) -> int:
    for value in values:
        if value is None:
            continue
        return int(value)
    return int(default)


def _coerce_float(*values: Any, default: float) -> float:
    for value in values:
        if value is None:
            continue
        return float(value)
    return float(default)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


@dataclass(slots=True, frozen=True)
class OperationsConfig:
    """Thresholds used by the operational alert evaluator."""

    runtime_startup_timeout_seconds: int = 120
    event_age_threshold_seconds: float = 15.0
    exchange_age_threshold_seconds: float = 15.0
    queue_lag_threshold_seconds: float = 5.0
    queue_depth_threshold: int = 250
    reconnect_count_threshold: int = 5
    dropped_event_count_threshold: int = 10
    model_staleness_warning_hours: float = 48.0
    evaluation_interval_seconds: float = 15.0
    critical_persistence_samples: int = 3

    def __post_init__(self) -> None:
        if self.runtime_startup_timeout_seconds <= 0:
            raise ValueError("runtime_startup_timeout_seconds must be positive")
        if self.event_age_threshold_seconds <= 0:
            raise ValueError("event_age_threshold_seconds must be positive")
        if self.exchange_age_threshold_seconds <= 0:
            raise ValueError("exchange_age_threshold_seconds must be positive")
        if self.queue_lag_threshold_seconds <= 0:
            raise ValueError("queue_lag_threshold_seconds must be positive")
        if self.queue_depth_threshold <= 0:
            raise ValueError("queue_depth_threshold must be positive")
        if self.reconnect_count_threshold < 0:
            raise ValueError("reconnect_count_threshold cannot be negative")
        if self.dropped_event_count_threshold < 0:
            raise ValueError("dropped_event_count_threshold cannot be negative")
        if self.model_staleness_warning_hours <= 0:
            raise ValueError("model_staleness_warning_hours must be positive")
        if self.evaluation_interval_seconds <= 0:
            raise ValueError("evaluation_interval_seconds must be positive")
        if self.critical_persistence_samples <= 0:
            raise ValueError("critical_persistence_samples must be positive")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "OperationsConfig":
        mapping = _as_mapping(data)
        return cls(
            runtime_startup_timeout_seconds=_coerce_int(
                mapping.get("runtime_startup_timeout_seconds"),
                default=120,
            ),
            event_age_threshold_seconds=_coerce_float(
                mapping.get("event_age_threshold_seconds"),
                default=15.0,
            ),
            exchange_age_threshold_seconds=_coerce_float(
                mapping.get("exchange_age_threshold_seconds"),
                default=15.0,
            ),
            queue_lag_threshold_seconds=_coerce_float(
                mapping.get("queue_lag_threshold_seconds"),
                default=5.0,
            ),
            queue_depth_threshold=_coerce_int(
                mapping.get("queue_depth_threshold"),
                default=250,
            ),
            reconnect_count_threshold=_coerce_int(
                mapping.get("reconnect_count_threshold"),
                default=5,
            ),
            dropped_event_count_threshold=_coerce_int(
                mapping.get("dropped_event_count_threshold"),
                default=10,
            ),
            model_staleness_warning_hours=_coerce_float(
                mapping.get("model_staleness_warning_hours"),
                default=48.0,
            ),
            evaluation_interval_seconds=_coerce_float(
                mapping.get("evaluation_interval_seconds"),
                default=15.0,
            ),
            critical_persistence_samples=_coerce_int(
                mapping.get("critical_persistence_samples"),
                default=3,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_startup_timeout_seconds": self.runtime_startup_timeout_seconds,
            "event_age_threshold_seconds": self.event_age_threshold_seconds,
            "exchange_age_threshold_seconds": self.exchange_age_threshold_seconds,
            "queue_lag_threshold_seconds": self.queue_lag_threshold_seconds,
            "queue_depth_threshold": self.queue_depth_threshold,
            "reconnect_count_threshold": self.reconnect_count_threshold,
            "dropped_event_count_threshold": self.dropped_event_count_threshold,
            "model_staleness_warning_hours": self.model_staleness_warning_hours,
            "evaluation_interval_seconds": self.evaluation_interval_seconds,
            "critical_persistence_samples": self.critical_persistence_samples,
        }


@dataclass(slots=True, frozen=True)
class OperationalAlertRecord:
    """Canonical representation of a single operational alert."""

    code: str
    severity: str
    source: str
    message: str
    recommended_action: str
    started_at: datetime | None = None
    last_seen_at: datetime | None = None
    active: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", str(self.code))
        object.__setattr__(self, "severity", str(self.severity))
        object.__setattr__(self, "source", str(self.source))
        object.__setattr__(self, "message", str(self.message))
        object.__setattr__(self, "recommended_action", str(self.recommended_action))
        if not self.code.strip():
            raise ValueError("alert code must not be empty")
        if self.severity not in ALERT_SEVERITIES:
            raise ValueError(f"invalid alert severity: {self.severity}")
        if not self.source.strip():
            raise ValueError("alert source must not be empty")
        if not self.message.strip():
            raise ValueError("alert message must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "source": self.source,
            "message": self.message,
            "recommended_action": self.recommended_action,
            "started_at": (
                self.started_at.isoformat() if self.started_at is not None else None
            ),
            "last_seen_at": (
                self.last_seen_at.isoformat() if self.last_seen_at is not None else None
            ),
            "active": self.active,
        }


@dataclass(slots=True, frozen=True)
class OperationalAlertState:
    """Operator-facing operational alert summary."""

    operational_alerts: tuple[OperationalAlertRecord, ...] = field(
        default_factory=tuple
    )
    halt_state: str = HALT_STATE_NONE
    entries_blocked_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "operational_alerts", tuple(self.operational_alerts))
        object.__setattr__(self, "halt_state", str(self.halt_state))
        if self.halt_state not in HALT_STATES:
            raise ValueError(f"invalid halt state: {self.halt_state}")
        if self.entries_blocked_reason is not None:
            object.__setattr__(
                self,
                "entries_blocked_reason",
                str(self.entries_blocked_reason),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "operational_alerts": [
                alert.to_dict() for alert in self.operational_alerts
            ],
            "halt_state": self.halt_state,
            "entries_blocked_reason": self.entries_blocked_reason,
        }
