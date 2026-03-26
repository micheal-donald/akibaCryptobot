"""Operator-facing rollout summary helpers."""

from __future__ import annotations

from runtime.view_models import ProjectionSource


def build_rollout_summary(projection) -> dict[str, object]:
    """Summarize rollout health for API, websocket, and Telegram surfaces."""

    runtime_is_healthy = getattr(projection, "runtime_is_healthy", True)
    runtime_start_status = getattr(projection, "runtime_start_status", "disabled")
    runtime_start_pending = getattr(projection, "runtime_start_pending", False)
    runtime_owns_entries = getattr(projection, "runtime_owns_entries", False)
    runtime_owns_exits = getattr(projection, "runtime_owns_exits", False)
    state_source = getattr(projection, "source", ProjectionSource.LEGACY).value

    summary = {
        "runtime_is_healthy": runtime_is_healthy,
        "runtime_start_status": runtime_start_status,
        "runtime_start_pending": runtime_start_pending,
    }

    if state_source == ProjectionSource.LEGACY.value:
        summary.update(
            {
                "rollout_health": "legacy",
                "rollout_recommended_action": "stay_legacy",
                "rollout_summary": "Legacy loop remains authoritative.",
            }
        )
        return summary

    if runtime_start_pending:
        summary.update(
            {
                "rollout_health": "startup_pending",
                "rollout_recommended_action": "wait_for_runtime",
                "rollout_summary": (
                    "Runtime startup is still in progress; wait for readiness "
                    "before evaluating promotion or rollback."
                ),
            }
        )
        return summary

    if not runtime_is_healthy:
        summary.update(
            {
                "rollout_health": "rollback_recommended",
                "rollout_recommended_action": "rollback_to_legacy",
                "rollout_summary": (
                    "Runtime is unhealthy; rollback to legacy is recommended."
                ),
            }
        )
        return summary

    if runtime_owns_exits:
        summary.update(
            {
                "rollout_health": "promotion_ready",
                "rollout_recommended_action": "stay_runtime_primary",
                "rollout_summary": (
                    "Runtime primary ownership is healthy and ready for operator "
                    "review."
                ),
            }
        )
        return summary

    if runtime_owns_entries:
        summary.update(
            {
                "rollout_health": "promotion_ready",
                "rollout_recommended_action": "promote_runtime_primary",
                "rollout_summary": (
                    "Runtime entry ownership is healthy and ready for promotion."
                ),
            }
        )
        return summary

    summary.update(
        {
            "rollout_health": "shadow_ready",
            "rollout_recommended_action": "continue_shadow",
            "rollout_summary": (
                "Runtime is healthy in shadow mode; validate before promotion."
            ),
        }
    )
    return summary
