from __future__ import annotations

from datetime import datetime

from operations.alerts import (
    ALERT_SEVERITY_CRITICAL,
    ALERT_SEVERITY_WARNING,
    HALT_STATE_ENTRIES_BLOCKED,
    OperationalAlertRecord,
    OperationalAlertState,
)
from runtime.config import RuntimeConfig


def test_runtime_config_exposes_default_operations_thresholds() -> None:
    config = RuntimeConfig.from_mapping({})

    assert config.operations.runtime_startup_timeout_seconds == 120
    assert config.operations.event_age_threshold_seconds == 15.0
    assert config.operations.exchange_age_threshold_seconds == 15.0
    assert config.operations.queue_lag_threshold_seconds == 5.0
    assert config.operations.queue_depth_threshold == 250
    assert config.operations.reconnect_count_threshold == 5
    assert config.operations.dropped_event_count_threshold == 10
    assert config.operations.model_staleness_warning_hours == 48.0
    assert config.operations.evaluation_interval_seconds == 15.0
    assert config.operations.critical_persistence_samples == 3


def test_runtime_config_parses_custom_operations_thresholds() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "operations": {
                "runtime_startup_timeout_seconds": 300,
                "event_age_threshold_seconds": 22,
                "exchange_age_threshold_seconds": 30,
                "queue_lag_threshold_seconds": 8,
                "queue_depth_threshold": 500,
                "reconnect_count_threshold": 7,
                "dropped_event_count_threshold": 12,
                "model_staleness_warning_hours": 72,
                "evaluation_interval_seconds": 20,
                "critical_persistence_samples": 4,
            }
        }
    )

    assert config.operations.runtime_startup_timeout_seconds == 300
    assert config.operations.event_age_threshold_seconds == 22.0
    assert config.operations.exchange_age_threshold_seconds == 30.0
    assert config.operations.queue_lag_threshold_seconds == 8.0
    assert config.operations.queue_depth_threshold == 500
    assert config.operations.reconnect_count_threshold == 7
    assert config.operations.dropped_event_count_threshold == 12
    assert config.operations.model_staleness_warning_hours == 72.0
    assert config.operations.evaluation_interval_seconds == 20.0
    assert config.operations.critical_persistence_samples == 4


def test_operational_alert_state_serializes_alerts_and_halt_state() -> None:
    alert = OperationalAlertRecord(
        code="runtime_feed_stale",
        severity=ALERT_SEVERITY_CRITICAL,
        source="runtime",
        message="Market data feed has been stale for 20s.",
        recommended_action="block_entries",
        started_at=datetime(2026, 4, 6, 10, 0, 0),
        last_seen_at=datetime(2026, 4, 6, 10, 0, 15),
        active=True,
    )
    warning = OperationalAlertRecord(
        code="model_stale",
        severity=ALERT_SEVERITY_WARNING,
        source="model",
        message="FreqAI model age exceeded the warning threshold.",
        recommended_action="refresh_model",
        started_at=datetime(2026, 4, 6, 9, 30, 0),
        last_seen_at=datetime(2026, 4, 6, 10, 0, 15),
        active=False,
    )
    state = OperationalAlertState(
        operational_alerts=(alert, warning),
        halt_state=HALT_STATE_ENTRIES_BLOCKED,
        entries_blocked_reason="runtime_feed_stale",
    )

    payload = state.to_dict()

    assert payload["halt_state"] == HALT_STATE_ENTRIES_BLOCKED
    assert payload["entries_blocked_reason"] == "runtime_feed_stale"
    assert payload["operational_alerts"][0]["code"] == "runtime_feed_stale"
    assert payload["operational_alerts"][0]["severity"] == ALERT_SEVERITY_CRITICAL
    assert payload["operational_alerts"][0]["started_at"] == "2026-04-06T10:00:00"
    assert payload["operational_alerts"][1]["active"] is False
