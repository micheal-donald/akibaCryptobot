"""Pure operational alert evaluator for runtime and model snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .alerts import (
    ALERT_SEVERITY_CRITICAL,
    ALERT_SEVERITY_WARNING,
    HALT_STATE_ENTRIES_BLOCKED,
    HALT_STATE_NONE,
    OperationalAlertRecord,
    OperationalAlertState,
    OperationsConfig,
)

ALERT_RUNTIME_UNHEALTHY = "runtime_unhealthy"
ALERT_RUNTIME_START_TIMEOUT = "runtime_start_timeout"
ALERT_RUNTIME_QUEUE_BACKLOG = "runtime_queue_backlog"
ALERT_RUNTIME_FEED_STALE = "runtime_feed_stale"
ALERT_RUNTIME_RECONNECT_STORM = "runtime_reconnect_storm"
ALERT_RUNTIME_EVENT_DROP_DETECTED = "runtime_event_drop_detected"
ALERT_MODEL_STALE = "model_stale"
ALERT_TRAINING_FAILED = "training_failed"
ALERT_TRAINING_STUCK = "training_stuck"
ALERT_NATIVE_SHADOW_DIVERGENT = "native_shadow_divergent"

CRITICAL_ALERT_CODES = {
    ALERT_RUNTIME_UNHEALTHY,
    ALERT_RUNTIME_START_TIMEOUT,
    ALERT_RUNTIME_QUEUE_BACKLOG,
    ALERT_RUNTIME_FEED_STALE,
    ALERT_RUNTIME_RECONNECT_STORM,
    ALERT_RUNTIME_EVENT_DROP_DETECTED,
    ALERT_TRAINING_FAILED,
    ALERT_TRAINING_STUCK,
}

WARNING_ALERT_CODES = {
    ALERT_MODEL_STALE,
    ALERT_NATIVE_SHADOW_DIVERGENT,
}


@dataclass(slots=True)
class _AlertCandidate:
    code: str
    severity: str
    source: str
    message: str
    recommended_action: str
    persist_samples: int = 1
    observed_hits: int = 1


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return (
            parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        )
    return None


def _get_first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _nested_mapping(mapping: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    return _as_mapping(mapping.get(key))


def _latency_last_seconds(
    runtime_telemetry: Mapping[str, Any],
    *,
    latency_key: str,
) -> float | None:
    latency = _nested_mapping(runtime_telemetry, "latency")
    summary = _as_mapping(latency.get(latency_key))
    if summary:
        last_ms = _as_float(summary.get("last_ms"))
        if last_ms is not None:
            return last_ms / 1000.0
    queue_summaries = _as_mapping(summary) if latency_key == "queue_lag_ms" else {}
    candidates = [
        _as_float(_as_mapping(queue_summary).get("last_ms"))
        for queue_summary in queue_summaries.values()
    ]
    valid = [value for value in candidates if value is not None]
    if not valid:
        return None
    return max(valid) / 1000.0


def _metric_count(runtime_telemetry: Mapping[str, Any], prefix: str) -> float | None:
    metric_counts = _nested_mapping(runtime_telemetry, "metric_counts")
    if prefix in metric_counts:
        return _as_float(metric_counts.get(prefix))
    matched = [
        _as_float(value)
        for key, value in metric_counts.items()
        if str(key).startswith(f"{prefix}.")
    ]
    valid = [value for value in matched if value is not None]
    if not valid:
        return None
    return sum(valid)


class OperationalAlertEvaluator:
    """Stateful but side-effect-free operational alert evaluator."""

    def __init__(self, operations: OperationsConfig | Mapping[str, Any] | None = None):
        self.operations = (
            operations
            if isinstance(operations, OperationsConfig)
            else OperationsConfig.from_mapping(_as_mapping(operations))
        )
        self._activation_counts: dict[str, int] = {}
        self._active_alerts: dict[str, OperationalAlertRecord] = {}
        self._training_signature: tuple[str, float | None, str | None] | None = None
        self._training_stuck_count: int = 0

    def evaluate(
        self,
        *,
        runtime_projection: Mapping[str, Any] | None = None,
        runtime_telemetry: Mapping[str, Any] | None = None,
        native_shadow_report: Mapping[str, Any] | None = None,
        circuit_breaker_state: Mapping[str, Any] | None = None,
        model_status: Mapping[str, Any] | None = None,
        training_job_status: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> OperationalAlertState:
        current_time = now or datetime.now(timezone.utc)
        runtime_projection = _as_mapping(runtime_projection)
        runtime_telemetry = _as_mapping(runtime_telemetry)
        native_shadow_report = _as_mapping(native_shadow_report)
        circuit_breaker_state = _as_mapping(circuit_breaker_state)
        model_status = _as_mapping(model_status)
        training_job_status = _as_mapping(training_job_status)

        candidates: dict[str, _AlertCandidate] = {}
        self._collect_runtime_candidates(
            candidates,
            runtime_projection=runtime_projection,
            runtime_telemetry=runtime_telemetry,
            current_time=current_time,
        )
        self._collect_model_candidates(
            candidates,
            model_status=model_status,
            current_time=current_time,
        )
        self._collect_training_candidates(
            candidates,
            training_job_status=training_job_status,
            current_time=current_time,
        )
        self._collect_native_shadow_candidates(
            candidates,
            native_shadow_report=native_shadow_report,
        )

        active_alerts = self._advance_alerts(candidates, current_time)
        entries_blocked_reason = next(
            (
                alert.code
                for alert in active_alerts
                if alert.severity == ALERT_SEVERITY_CRITICAL
            ),
            None,
        )
        halt_state = (
            HALT_STATE_ENTRIES_BLOCKED if entries_blocked_reason else HALT_STATE_NONE
        )

        # Keep circuit-breaker input available for future wiring without altering
        # the pure evaluator result shape in this phase.
        _ = circuit_breaker_state

        return OperationalAlertState(
            operational_alerts=tuple(active_alerts),
            halt_state=halt_state,
            entries_blocked_reason=entries_blocked_reason,
        )

    def _collect_runtime_candidates(
        self,
        candidates: dict[str, _AlertCandidate],
        *,
        runtime_projection: Mapping[str, Any],
        runtime_telemetry: Mapping[str, Any],
        current_time: datetime,
    ) -> None:
        runtime_is_running = _truthy(
            _get_first(runtime_projection, "runtime_is_running", "is_running")
        )
        runtime_is_healthy = _truthy(
            _get_first(runtime_projection, "runtime_is_healthy", "healthy")
        )
        runtime_start_pending = _truthy(
            _get_first(
                runtime_projection,
                "runtime_start_pending",
                "start_pending",
                "startup_pending",
            )
        )
        start_status = (
            str(
                _get_first(
                    runtime_projection,
                    "runtime_start_status",
                    "start_status",
                    "startup_status",
                )
                or ""
            )
            .strip()
            .lower()
        )
        started_at = _parse_datetime(
            _get_first(
                runtime_projection,
                "runtime_start_started_at",
                "runtime_start_pending_since",
                "runtime_start_time",
                "started_at",
            )
        )
        if runtime_start_pending:
            if started_at is not None:
                elapsed = (current_time - started_at).total_seconds()
                if elapsed >= self.operations.runtime_startup_timeout_seconds:
                    candidates[ALERT_RUNTIME_START_TIMEOUT] = _AlertCandidate(
                        code=ALERT_RUNTIME_START_TIMEOUT,
                        severity=ALERT_SEVERITY_CRITICAL,
                        source="runtime",
                        message=(
                            "Runtime startup exceeded "
                            f"{self.operations.runtime_startup_timeout_seconds}s."
                        ),
                        recommended_action="inspect_runtime_startup",
                        persist_samples=self.operations.critical_persistence_samples,
                    )
                    return
            if start_status in {"timed_out", "timeout", "failed"}:
                candidates[ALERT_RUNTIME_START_TIMEOUT] = _AlertCandidate(
                    code=ALERT_RUNTIME_START_TIMEOUT,
                    severity=ALERT_SEVERITY_CRITICAL,
                    source="runtime",
                    message="Runtime startup timed out.",
                    recommended_action="inspect_runtime_startup",
                    persist_samples=self.operations.critical_persistence_samples,
                )
                return

        if not runtime_is_running or not runtime_is_healthy:
            candidates[ALERT_RUNTIME_UNHEALTHY] = _AlertCandidate(
                code=ALERT_RUNTIME_UNHEALTHY,
                severity=ALERT_SEVERITY_CRITICAL,
                source="runtime",
                message="Runtime reports unhealthy or not running.",
                recommended_action="inspect_runtime_health",
                persist_samples=self.operations.critical_persistence_samples,
            )

        queue_lag = _as_float(
            _get_first(
                runtime_telemetry,
                "queue_lag_seconds",
                "queue_lag",
                "event_queue_lag_seconds",
            )
        )
        if queue_lag is None:
            queue_lag = _latency_last_seconds(
                runtime_telemetry,
                latency_key="queue_lag_ms",
            )
        queue_depth = _as_float(
            _get_first(
                runtime_telemetry,
                "queue_depth",
                "queue_size",
                "event_queue_depth",
            )
        )
        if queue_depth is None:
            queue_depths = _nested_mapping(runtime_telemetry, "queue_depths")
            queue_depth = max(
                (
                    value
                    for value in (_as_float(item) for item in queue_depths.values())
                    if value is not None
                ),
                default=None,
            )
        if (
            queue_lag is not None
            and queue_lag >= self.operations.queue_lag_threshold_seconds
        ) or (
            queue_depth is not None
            and queue_depth >= self.operations.queue_depth_threshold
        ):
            candidates[ALERT_RUNTIME_QUEUE_BACKLOG] = _AlertCandidate(
                code=ALERT_RUNTIME_QUEUE_BACKLOG,
                severity=ALERT_SEVERITY_CRITICAL,
                source="telemetry",
                message="Runtime queue backlog exceeds configured threshold.",
                recommended_action="reduce_runtime_load",
                persist_samples=self.operations.critical_persistence_samples,
            )

        event_age = _as_float(
            _get_first(
                runtime_telemetry,
                "event_age_seconds",
                "event_age",
            )
        )
        if event_age is None:
            event_age = _latency_last_seconds(
                runtime_telemetry,
                latency_key="event_age_ms",
            )
        exchange_age = _as_float(
            _get_first(
                runtime_telemetry,
                "exchange_age_seconds",
                "exchange_age",
            )
        )
        if exchange_age is None:
            exchange_age = _latency_last_seconds(
                runtime_telemetry,
                latency_key="exchange_age_ms",
            )
        if (
            event_age is not None
            and event_age >= self.operations.event_age_threshold_seconds
        ) or (
            exchange_age is not None
            and exchange_age >= self.operations.exchange_age_threshold_seconds
        ):
            candidates[ALERT_RUNTIME_FEED_STALE] = _AlertCandidate(
                code=ALERT_RUNTIME_FEED_STALE,
                severity=ALERT_SEVERITY_CRITICAL,
                source="telemetry",
                message="Runtime feed age exceeds configured threshold.",
                recommended_action="inspect_market_data_feed",
                persist_samples=self.operations.critical_persistence_samples,
            )

        reconnect_count = _as_float(
            _get_first(
                runtime_telemetry,
                "reconnect_count",
                "reconnects",
                "reconnect_storm_count",
            )
        )
        if reconnect_count is None:
            reconnect_count = _metric_count(runtime_telemetry, "reconnect")
        if (
            reconnect_count is not None
            and reconnect_count >= self.operations.reconnect_count_threshold
        ):
            candidates[ALERT_RUNTIME_RECONNECT_STORM] = _AlertCandidate(
                code=ALERT_RUNTIME_RECONNECT_STORM,
                severity=ALERT_SEVERITY_CRITICAL,
                source="telemetry",
                message="Runtime reconnect count exceeds configured threshold.",
                recommended_action="inspect_transport_stability",
                persist_samples=self.operations.critical_persistence_samples,
            )

        dropped_event_count = _as_float(
            _get_first(
                runtime_telemetry,
                "dropped_event_count",
                "dropped_events",
                "event_drop_count",
            )
        )
        if dropped_event_count is None:
            dropped_event_count = _metric_count(runtime_telemetry, "dropped_event")
        if (
            dropped_event_count is not None
            and dropped_event_count >= self.operations.dropped_event_count_threshold
        ):
            candidates[ALERT_RUNTIME_EVENT_DROP_DETECTED] = _AlertCandidate(
                code=ALERT_RUNTIME_EVENT_DROP_DETECTED,
                severity=ALERT_SEVERITY_CRITICAL,
                source="telemetry",
                message="Dropped event count exceeds configured threshold.",
                recommended_action="inspect_event_pipeline",
                persist_samples=self.operations.critical_persistence_samples,
            )

    def _collect_model_candidates(
        self,
        candidates: dict[str, _AlertCandidate],
        *,
        model_status: Mapping[str, Any],
        current_time: datetime,
    ) -> None:
        if _truthy(model_status.get("stale")):
            candidates[ALERT_MODEL_STALE] = _AlertCandidate(
                code=ALERT_MODEL_STALE,
                severity=ALERT_SEVERITY_WARNING,
                source="model",
                message="FreqAI model reports stale.",
                recommended_action="refresh_freqai_model",
            )
            return

        age_hours = _as_float(
            _get_first(
                model_status,
                "age_hours",
                "model_age_hours",
            )
        )
        if age_hours is None:
            models = model_status.get("models")
            if isinstance(models, list):
                model_ages = [
                    _as_float(_as_mapping(model).get("age_hours")) for model in models
                ]
                valid_model_ages = [value for value in model_ages if value is not None]
                if valid_model_ages:
                    age_hours = max(valid_model_ages)
                elif any(_truthy(_as_mapping(model).get("stale")) for model in models):
                    candidates[ALERT_MODEL_STALE] = _AlertCandidate(
                        code=ALERT_MODEL_STALE,
                        severity=ALERT_SEVERITY_WARNING,
                        source="model",
                        message="At least one FreqAI model reports stale.",
                        recommended_action="refresh_freqai_model",
                    )
                    return
        if age_hours is None:
            last_success_at = _parse_datetime(
                _get_first(model_status, "last_success_at", "last_trained")
            )
            if last_success_at is not None:
                age_hours = (current_time - last_success_at).total_seconds() / 3600.0

        if (
            age_hours is not None
            and age_hours >= self.operations.model_staleness_warning_hours
        ):
            candidates[ALERT_MODEL_STALE] = _AlertCandidate(
                code=ALERT_MODEL_STALE,
                severity=ALERT_SEVERITY_WARNING,
                source="model",
                message="FreqAI model age exceeds the warning threshold.",
                recommended_action="refresh_freqai_model",
            )

    def _collect_training_candidates(
        self,
        candidates: dict[str, _AlertCandidate],
        *,
        training_job_status: Mapping[str, Any],
        current_time: datetime,
    ) -> None:
        current_job = training_job_status.get("current_job")
        last_job = training_job_status.get("last_job")
        current_job = _as_mapping(current_job)
        last_job = _as_mapping(last_job)

        active_job = current_job if current_job else None
        if active_job is None and last_job.get("status") == "running":
            active_job = last_job

        if active_job is None:
            self._training_signature = None
            self._training_stuck_count = 0
            if _get_first(last_job, "status") == "failed":
                candidates[ALERT_TRAINING_FAILED] = _AlertCandidate(
                    code=ALERT_TRAINING_FAILED,
                    severity=ALERT_SEVERITY_CRITICAL,
                    source="training",
                    message="Most recent training job failed.",
                    recommended_action="inspect_training_job",
                    persist_samples=self.operations.critical_persistence_samples,
                )
            return

        status = str(_get_first(active_job, "status") or "").strip().lower()
        job_id = str(_get_first(active_job, "job_id") or "")
        progress = _as_float(_get_first(active_job, "progress"))
        signature = (job_id, progress, status)

        if status == "failed":
            candidates[ALERT_TRAINING_FAILED] = _AlertCandidate(
                code=ALERT_TRAINING_FAILED,
                severity=ALERT_SEVERITY_CRITICAL,
                source="training",
                message="Active training job failed.",
                recommended_action="inspect_training_job",
                persist_samples=self.operations.critical_persistence_samples,
            )
            self._training_signature = None
            self._training_stuck_count = 0
            return

        if status != "running":
            self._training_signature = None
            self._training_stuck_count = 0
            return

        if self._training_signature == signature:
            self._training_stuck_count += 1
        else:
            self._training_signature = signature
            self._training_stuck_count = 1

        started_at = _parse_datetime(
            _get_first(active_job, "started_at", "requested_at")
        )
        if started_at is not None:
            elapsed_hours = (current_time - started_at).total_seconds() / 3600.0
            if elapsed_hours >= self.operations.model_staleness_warning_hours * 4:
                candidates[ALERT_TRAINING_STUCK] = _AlertCandidate(
                    code=ALERT_TRAINING_STUCK,
                    severity=ALERT_SEVERITY_CRITICAL,
                    source="training",
                    message="Training job has exceeded its expected duration.",
                    recommended_action="inspect_training_job",
                    persist_samples=self.operations.critical_persistence_samples,
                    observed_hits=self.operations.critical_persistence_samples,
                )
                return

        if self._training_stuck_count >= self.operations.critical_persistence_samples:
            candidates[ALERT_TRAINING_STUCK] = _AlertCandidate(
                code=ALERT_TRAINING_STUCK,
                severity=ALERT_SEVERITY_CRITICAL,
                source="training",
                message="Training job appears stuck across repeated evaluations.",
                recommended_action="inspect_training_job",
                persist_samples=self.operations.critical_persistence_samples,
                observed_hits=self._training_stuck_count,
            )

    def _collect_native_shadow_candidates(
        self,
        candidates: dict[str, _AlertCandidate],
        *,
        native_shadow_report: Mapping[str, Any],
    ) -> None:
        divergent = _truthy(
            _get_first(
                native_shadow_report,
                "divergent",
                "is_divergent",
                "mismatch_detected",
                "native_shadow_is_match",
            )
        )
        if "native_shadow_is_match" in native_shadow_report:
            divergent = not _truthy(native_shadow_report.get("native_shadow_is_match"))
        mismatch_count = _as_float(_get_first(native_shadow_report, "mismatch_count"))
        if mismatch_count is None:
            mismatch_count = _as_float(
                _get_first(native_shadow_report, "native_shadow_mismatch_count")
            )
        status = str(_get_first(native_shadow_report, "status") or "").strip().lower()
        if not status:
            status = (
                str(_get_first(native_shadow_report, "native_shadow_status") or "")
                .strip()
                .lower()
            )
        if (
            divergent
            or (mismatch_count is not None and mismatch_count > 0)
            or status
            in {
                "divergent",
                "mismatch",
                "drift",
            }
        ):
            candidates[ALERT_NATIVE_SHADOW_DIVERGENT] = _AlertCandidate(
                code=ALERT_NATIVE_SHADOW_DIVERGENT,
                severity=ALERT_SEVERITY_WARNING,
                source="native_shadow",
                message="Native shadow comparison diverged from the Python runtime.",
                recommended_action="inspect_native_shadow_boundary",
            )

    def _advance_alerts(
        self,
        candidates: Mapping[str, _AlertCandidate],
        current_time: datetime,
    ) -> list[OperationalAlertRecord]:
        for code in list(self._active_alerts):
            if code not in candidates:
                self._active_alerts.pop(code, None)
                self._activation_counts.pop(code, None)

        active_alerts: list[OperationalAlertRecord] = []
        for code, candidate in candidates.items():
            previous_hits = self._activation_counts.get(code, 0)
            if candidate.severity == ALERT_SEVERITY_WARNING:
                next_hits = 1
            else:
                next_hits = previous_hits + 1
            self._activation_counts[code] = next_hits

            effective_hits = max(next_hits, candidate.observed_hits)
            should_activate = candidate.severity == ALERT_SEVERITY_WARNING or (
                effective_hits >= candidate.persist_samples
            )
            if not should_activate:
                continue

            prior = self._active_alerts.get(code)
            started_at = prior.started_at if prior is not None else current_time
            active = OperationalAlertRecord(
                code=code,
                severity=candidate.severity,
                source=candidate.source,
                message=candidate.message,
                recommended_action=candidate.recommended_action,
                started_at=started_at,
                last_seen_at=current_time,
                active=True,
            )
            self._active_alerts[code] = active
            active_alerts.append(active)

        active_alerts.sort(key=lambda item: (item.severity, item.code))
        return active_alerts
