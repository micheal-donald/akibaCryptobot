"""Training job state models used by the training control plane."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

TRAINING_JOB_IDLE = "idle"
TRAINING_JOB_QUEUED = "queued"
TRAINING_JOB_RUNNING = "running"
TRAINING_JOB_SUCCEEDED = "succeeded"
TRAINING_JOB_FAILED = "failed"
TRAINING_JOB_BLOCKED = "blocked"

TRAINING_JOB_STATUSES = {
    TRAINING_JOB_IDLE,
    TRAINING_JOB_QUEUED,
    TRAINING_JOB_RUNNING,
    TRAINING_JOB_SUCCEEDED,
    TRAINING_JOB_FAILED,
    TRAINING_JOB_BLOCKED,
}

TRAINING_JOB_REQUESTERS = {"api", "telegram", "system"}
ACTIVE_TRAINING_JOB_STATUSES = {TRAINING_JOB_QUEUED, TRAINING_JOB_RUNNING}


def _normalize_optional_timestamp(value: datetime | int | float | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return int(value.timestamp())
    return int(value)


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_values(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(str(value) for value in values if str(value))


@dataclass(slots=True)
class TrainingJobRequest:
    """Incoming request parameters for a training job."""

    pairs: tuple[str, ...] = field(default_factory=tuple)
    candles: int = 1000
    timeframe: str = "1h"
    requested_by: str = "system"

    def __post_init__(self) -> None:
        self.pairs = _normalize_values(self.pairs)
        self.candles = int(self.candles)
        self.timeframe = str(self.timeframe)
        self.requested_by = str(self.requested_by)
        if self.requested_by not in TRAINING_JOB_REQUESTERS:
            raise ValueError(
                f"requested_by must be one of {sorted(TRAINING_JOB_REQUESTERS)}"
            )
        if self.candles <= 0:
            raise ValueError("candles must be positive")
        if not self.timeframe:
            raise ValueError("timeframe must not be empty")


@dataclass(slots=True)
class TrainingJobRecord:
    """Persisted training job row."""

    job_id: str = field(default_factory=lambda: uuid4().hex)
    status: str = TRAINING_JOB_QUEUED
    requested_by: str = "system"
    requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    pairs: tuple[str, ...] = field(default_factory=tuple)
    candles: int = 1000
    timeframe: str = "1h"
    progress: float = 0.0
    result_summary: str | None = None
    error: str | None = None
    promoted_model_paths: tuple[str, ...] = field(default_factory=tuple)
    reload_status: str | None = None

    def __post_init__(self) -> None:
        self.job_id = str(self.job_id)
        self.status = str(self.status)
        if self.status not in TRAINING_JOB_STATUSES:
            raise ValueError(f"invalid training job status: {self.status}")
        self.requested_by = str(self.requested_by)
        if self.requested_by not in TRAINING_JOB_REQUESTERS:
            raise ValueError(
                f"requested_by must be one of {sorted(TRAINING_JOB_REQUESTERS)}"
            )
        self.pairs = _normalize_values(self.pairs)
        self.candles = int(self.candles)
        self.timeframe = str(self.timeframe)
        self.progress = float(self.progress)
        self.promoted_model_paths = _normalize_values(self.promoted_model_paths)
        self.requested_at = _normalize_datetime(self.requested_at)
        self.started_at = _normalize_datetime(self.started_at)
        self.finished_at = _normalize_datetime(self.finished_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "requested_by": self.requested_by,
            "requested_at": (
                self.requested_at.isoformat() if self.requested_at else None
            ),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "pairs": list(self.pairs),
            "candles": self.candles,
            "timeframe": self.timeframe,
            "progress": self.progress,
            "result_summary": self.result_summary,
            "error": self.error,
            "promoted_model_paths": list(self.promoted_model_paths),
            "reload_status": self.reload_status,
        }

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_TRAINING_JOB_STATUSES


@dataclass(slots=True)
class TrainingJobSnapshot:
    """Current and latest training job state for bot/operator surfaces."""

    current_job: TrainingJobRecord | None
    last_job: TrainingJobRecord | None
    can_start_training: bool
    active_job_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_job": (
                self.current_job.to_dict() if self.current_job is not None else None
            ),
            "last_job": self.last_job.to_dict() if self.last_job is not None else None,
            "can_start_training": self.can_start_training,
            "active_job_count": self.active_job_count,
        }
