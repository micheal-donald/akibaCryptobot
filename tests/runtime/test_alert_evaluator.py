from __future__ import annotations

from datetime import datetime, timezone

from operations.alert_evaluator import (
    ALERT_MODEL_STALE,
    ALERT_NATIVE_SHADOW_DIVERGENT,
    ALERT_RUNTIME_EVENT_DROP_DETECTED,
    ALERT_RUNTIME_FEED_STALE,
    ALERT_RUNTIME_QUEUE_BACKLOG,
    ALERT_RUNTIME_RECONNECT_STORM,
    ALERT_RUNTIME_START_TIMEOUT,
    ALERT_TRAINING_FAILED,
    ALERT_TRAINING_STUCK,
    OperationalAlertEvaluator,
)
from operations.alerts import (
    ALERT_SEVERITY_WARNING,
    HALT_STATE_ENTRIES_BLOCKED,
    HALT_STATE_NONE,
    OperationsConfig,
)


def _codes(result) -> set[str]:
    return {alert.code for alert in result.operational_alerts}


def _base_projection(**overrides):
    projection = {
        "runtime_is_running": True,
        "runtime_is_healthy": True,
        "runtime_start_pending": False,
    }
    projection.update(overrides)
    return projection


def test_alert_evaluator_triggers_threshold_crossings() -> None:
    evaluator = OperationalAlertEvaluator(
        OperationsConfig(critical_persistence_samples=1)
    )
    result = evaluator.evaluate(
        runtime_projection=_base_projection(),
        runtime_telemetry={
            "queue_lag_seconds": 9,
            "queue_depth": 300,
            "event_age_seconds": 20,
            "exchange_age_seconds": 30,
            "reconnect_count": 8,
            "dropped_event_count": 12,
        },
        native_shadow_report={"divergent": True},
        model_status={"stale": True},
        training_job_status={"last_job": {"status": "failed"}},
        now=datetime(2026, 4, 6, 10, 0, 0, tzinfo=timezone.utc),
    )

    assert _codes(result) == {
        ALERT_RUNTIME_QUEUE_BACKLOG,
        ALERT_RUNTIME_FEED_STALE,
        ALERT_RUNTIME_RECONNECT_STORM,
        ALERT_RUNTIME_EVENT_DROP_DETECTED,
        ALERT_MODEL_STALE,
        ALERT_TRAINING_FAILED,
        ALERT_NATIVE_SHADOW_DIVERGENT,
    }
    assert (
        next(
            alert
            for alert in result.operational_alerts
            if alert.code == ALERT_MODEL_STALE
        ).severity
        == ALERT_SEVERITY_WARNING
    )
    assert result.halt_state == HALT_STATE_ENTRIES_BLOCKED


def test_alert_evaluator_escalates_start_timeout_and_clears_on_recovery() -> None:
    evaluator = OperationalAlertEvaluator()
    pending_projection = _base_projection(
        runtime_is_running=False,
        runtime_is_healthy=False,
        runtime_start_pending=True,
        runtime_start_started_at=datetime(2026, 4, 6, 9, 55, 0, tzinfo=timezone.utc),
    )

    first = evaluator.evaluate(
        runtime_projection=pending_projection,
        runtime_telemetry={},
        native_shadow_report={},
        model_status={},
        training_job_status={},
        now=datetime(2026, 4, 6, 10, 0, 0, tzinfo=timezone.utc),
    )
    second = evaluator.evaluate(
        runtime_projection=pending_projection,
        runtime_telemetry={},
        native_shadow_report={},
        model_status={},
        training_job_status={},
        now=datetime(2026, 4, 6, 10, 0, 15, tzinfo=timezone.utc),
    )
    third = evaluator.evaluate(
        runtime_projection=pending_projection,
        runtime_telemetry={},
        native_shadow_report={},
        model_status={},
        training_job_status={},
        now=datetime(2026, 4, 6, 10, 0, 30, tzinfo=timezone.utc),
    )
    recovered = evaluator.evaluate(
        runtime_projection=_base_projection(),
        runtime_telemetry={},
        native_shadow_report={},
        model_status={},
        training_job_status={},
        now=datetime(2026, 4, 6, 10, 0, 45, tzinfo=timezone.utc),
    )

    assert _codes(first) == set()
    assert _codes(second) == set()
    assert _codes(third) == {ALERT_RUNTIME_START_TIMEOUT}
    assert third.halt_state == HALT_STATE_ENTRIES_BLOCKED
    assert _codes(recovered) == set()
    assert recovered.halt_state == HALT_STATE_NONE
    assert recovered.entries_blocked_reason is None


def test_alert_evaluator_detects_training_stuck_after_repeated_running_samples() -> (
    None
):
    evaluator = OperationalAlertEvaluator()
    running_job = {
        "current_job": {
            "job_id": "job-1",
            "status": "running",
            "progress": 42.0,
            "started_at": "2026-04-06T09:45:00+00:00",
        },
        "last_job": None,
    }

    first = evaluator.evaluate(
        runtime_projection=_base_projection(),
        runtime_telemetry={},
        native_shadow_report={},
        model_status={},
        training_job_status=running_job,
        now=datetime(2026, 4, 6, 10, 0, 0, tzinfo=timezone.utc),
    )
    second = evaluator.evaluate(
        runtime_projection=_base_projection(),
        runtime_telemetry={},
        native_shadow_report={},
        model_status={},
        training_job_status=running_job,
        now=datetime(2026, 4, 6, 10, 0, 15, tzinfo=timezone.utc),
    )
    third = evaluator.evaluate(
        runtime_projection=_base_projection(),
        runtime_telemetry={},
        native_shadow_report={},
        model_status={},
        training_job_status=running_job,
        now=datetime(2026, 4, 6, 10, 0, 30, tzinfo=timezone.utc),
    )

    assert _codes(first) == set()
    assert _codes(second) == set()
    assert _codes(third) == {ALERT_TRAINING_STUCK}

    cleared = evaluator.evaluate(
        runtime_projection=_base_projection(),
        runtime_telemetry={},
        native_shadow_report={},
        model_status={},
        training_job_status={"current_job": None, "last_job": None},
        now=datetime(2026, 4, 6, 10, 0, 45, tzinfo=timezone.utc),
    )

    assert _codes(cleared) == set()
    assert cleared.halt_state == HALT_STATE_NONE


def test_event_drop_alert_clears_when_drop_rate_subsides() -> None:
    """Drop alert must clear when no new drops arrive between evaluations.

    The previous implementation compared the cumulative drop counter against
    a fixed threshold. A monotonic counter never decreases, so once the alert
    tripped it stayed tripped forever — which is exactly how the live runtime
    ended up with ``halt_state=runtime_kill_switch`` permanently set after a
    transient burst of dropped market.tick persistence jobs. The evaluator
    now compares the delta since the previous evaluation, so a stable counter
    naturally clears the alert.
    """

    evaluator = OperationalAlertEvaluator(
        OperationsConfig(
            critical_persistence_samples=1,
            dropped_event_count_threshold=10,
        )
    )

    burst = evaluator.evaluate(
        runtime_projection=_base_projection(),
        runtime_telemetry={"dropped_event_count": 25},
        native_shadow_report={},
        model_status={},
        training_job_status={},
        now=datetime(2026, 4, 16, 11, 19, 0, tzinfo=timezone.utc),
    )
    quiet = evaluator.evaluate(
        runtime_projection=_base_projection(),
        runtime_telemetry={"dropped_event_count": 25},
        native_shadow_report={},
        model_status={},
        training_job_status={},
        now=datetime(2026, 4, 16, 11, 19, 15, tzinfo=timezone.utc),
    )

    assert _codes(burst) == {ALERT_RUNTIME_EVENT_DROP_DETECTED}
    assert burst.halt_state == HALT_STATE_ENTRIES_BLOCKED
    # No new drops in the second sample — the alert must clear without a
    # process restart.
    assert _codes(quiet) == set()
    assert quiet.halt_state == HALT_STATE_NONE
    assert quiet.entries_blocked_reason is None


def test_event_drop_alert_refires_on_sustained_drop_rate() -> None:
    """Sustained drop rate above threshold per interval keeps the alert active."""

    evaluator = OperationalAlertEvaluator(
        OperationsConfig(
            critical_persistence_samples=1,
            dropped_event_count_threshold=10,
        )
    )

    first = evaluator.evaluate(
        runtime_projection=_base_projection(),
        runtime_telemetry={"dropped_event_count": 25},
        native_shadow_report={},
        model_status={},
        training_job_status={},
        now=datetime(2026, 4, 16, 11, 19, 0, tzinfo=timezone.utc),
    )
    # Another 25 drops between samples → sustained rate above threshold.
    second = evaluator.evaluate(
        runtime_projection=_base_projection(),
        runtime_telemetry={"dropped_event_count": 50},
        native_shadow_report={},
        model_status={},
        training_job_status={},
        now=datetime(2026, 4, 16, 11, 19, 15, tzinfo=timezone.utc),
    )

    assert _codes(first) == {ALERT_RUNTIME_EVENT_DROP_DETECTED}
    assert _codes(second) == {ALERT_RUNTIME_EVENT_DROP_DETECTED}
    assert second.halt_state == HALT_STATE_ENTRIES_BLOCKED


def test_alert_evaluator_understands_real_runtime_snapshot_shapes() -> None:
    evaluator = OperationalAlertEvaluator(
        OperationsConfig(
            critical_persistence_samples=1,
            queue_lag_threshold_seconds=1.0,
            queue_depth_threshold=5,
            event_age_threshold_seconds=10.0,
            exchange_age_threshold_seconds=10.0,
            reconnect_count_threshold=3,
            dropped_event_count_threshold=2,
        )
    )

    result = evaluator.evaluate(
        runtime_projection=_base_projection(
            runtime_is_running=True, runtime_is_healthy=True
        ),
        runtime_telemetry={
            "queue_depths": {"runtime.event_bus": 6},
            "metric_counts": {
                "reconnect.market_data": 3,
                "dropped_event.persistence": 2,
            },
            "latency": {
                "event_age_ms": {"last_ms": 12500.0},
                "exchange_age_ms": {"last_ms": 15000.0},
                "queue_lag_ms": {
                    "runtime.event_bus": {"last_ms": 2200.0},
                },
            },
        },
        native_shadow_report={
            "native_shadow_status": "divergent",
            "native_shadow_is_match": False,
            "native_shadow_mismatch_count": 2,
        },
        model_status={
            "models": [
                {"name": "freqai", "age_hours": 60.0, "stale": True},
            ]
        },
        training_job_status={},
        now=datetime(2026, 4, 6, 10, 1, 0, tzinfo=timezone.utc),
    )

    assert _codes(result) == {
        ALERT_RUNTIME_QUEUE_BACKLOG,
        ALERT_RUNTIME_FEED_STALE,
        ALERT_RUNTIME_RECONNECT_STORM,
        ALERT_RUNTIME_EVENT_DROP_DETECTED,
        ALERT_MODEL_STALE,
        ALERT_NATIVE_SHADOW_DIVERGENT,
    }
