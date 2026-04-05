"""Operator-facing rollout summary helpers."""

from __future__ import annotations

from runtime.mode import DeploymentProfile, RuntimeMode
from runtime.view_models import ProjectionSource


def build_rollout_summary(
    projection,
    *,
    configured_deployment_profile: str | None = None,
    trading_mode: str | None = None,
) -> dict[str, object]:
    """Summarize rollout health for API, websocket, and Telegram surfaces."""

    runtime_is_healthy = getattr(projection, "runtime_is_healthy", True)
    runtime_start_status = getattr(projection, "runtime_start_status", "disabled")
    runtime_start_pending = getattr(projection, "runtime_start_pending", False)
    runtime_owns_entries = getattr(projection, "runtime_owns_entries", False)
    runtime_owns_exits = getattr(projection, "runtime_owns_exits", False)
    state_source = getattr(projection, "source", ProjectionSource.LEGACY).value
    runtime_mode = getattr(projection, "runtime_mode", RuntimeMode.LEGACY.value)
    deployment_profile = _resolve_deployment_profile(configured_deployment_profile)
    rollout_environment = _resolve_rollout_environment(
        deployment_profile=deployment_profile,
        trading_mode=trading_mode,
    )
    rollout_stage = _resolve_rollout_stage(
        deployment_profile=deployment_profile,
        rollout_environment=rollout_environment,
        runtime_mode=runtime_mode,
        state_source=state_source,
        runtime_owns_entries=runtime_owns_entries,
        runtime_owns_exits=runtime_owns_exits,
    )

    summary = {
        "runtime_is_healthy": runtime_is_healthy,
        "runtime_start_status": runtime_start_status,
        "runtime_start_pending": runtime_start_pending,
        "rollout_environment": rollout_environment,
        "rollout_stage": rollout_stage,
    }

    if state_source == ProjectionSource.LEGACY.value:
        if rollout_environment == "live":
            summary.update(
                {
                    "rollout_health": "legacy",
                    "rollout_recommended_action": "stay_live_legacy",
                    "rollout_summary": (
                        "Live rollout remains on the legacy engine; promote "
                        "through the reviewed live canary sequence before "
                        "runtime ownership."
                    ),
                }
            )
        else:
            summary.update(
                {
                    "rollout_health": "legacy",
                    "rollout_recommended_action": "stay_legacy",
                    "rollout_summary": "Legacy loop remains authoritative.",
                }
            )
        return summary

    if runtime_start_pending:
        if rollout_environment == "live":
            summary.update(
                {
                    "rollout_health": "startup_pending",
                    "rollout_recommended_action": "wait_for_live_runtime",
                    "rollout_summary": (
                        "Live runtime startup is still in progress; wait for "
                        "readiness before evaluating promotion or rollback."
                    ),
                }
            )
        else:
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
        if rollout_environment == "live":
            summary.update(
                {
                    "rollout_health": "rollback_recommended",
                    "rollout_recommended_action": "rollback_to_live_legacy",
                    "rollout_summary": (
                        "Live runtime is unhealthy; rollback to live_legacy is "
                        "recommended."
                    ),
                }
            )
        else:
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
        if rollout_environment == "live":
            summary.update(
                {
                    "rollout_health": "promotion_ready",
                    "rollout_recommended_action": "stay_live_runtime_primary",
                    "rollout_summary": (
                        "Live runtime primary ownership is healthy and ready "
                        "for operator review."
                    ),
                }
            )
        else:
            summary.update(
                {
                    "rollout_health": "promotion_ready",
                    "rollout_recommended_action": "stay_runtime_primary",
                    "rollout_summary": (
                        "Runtime primary ownership is healthy and ready for "
                        "operator review."
                    ),
                }
            )
        return summary

    if runtime_owns_entries:
        if rollout_environment == "live":
            summary.update(
                {
                    "rollout_health": "promotion_ready",
                    "rollout_recommended_action": "promote_live_runtime_primary",
                    "rollout_summary": (
                        "Live runtime entry ownership is healthy and ready for "
                        "primary promotion."
                    ),
                }
            )
        else:
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

    if rollout_environment == "live":
        summary.update(
            {
                "rollout_health": "shadow_ready",
                "rollout_recommended_action": "promote_live_runtime_entries",
                "rollout_summary": (
                    "Live runtime shadow canary is healthy and ready for "
                    "entry-ownership promotion."
                ),
            }
        )
    else:
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


def _resolve_deployment_profile(
    value: str | DeploymentProfile | None,
) -> DeploymentProfile | None:
    if value is None:
        return None
    try:
        return DeploymentProfile.parse(value)
    except ValueError:
        return None


def _resolve_rollout_environment(
    *,
    deployment_profile: DeploymentProfile | None,
    trading_mode: str | None,
) -> str:
    if deployment_profile is not None and deployment_profile.is_live:
        return "live"
    if str(trading_mode or "").strip().lower() == "live":
        return "live"
    return "paper"


def _resolve_rollout_stage(
    *,
    deployment_profile: DeploymentProfile | None,
    rollout_environment: str,
    runtime_mode: str,
    state_source: str,
    runtime_owns_entries: bool,
    runtime_owns_exits: bool,
) -> str:
    if deployment_profile is not None:
        if deployment_profile in {
            DeploymentProfile.PAPER_LEGACY,
            DeploymentProfile.LIVE_LEGACY,
        }:
            return "legacy"
        if deployment_profile is DeploymentProfile.PAPER_SHADOW:
            return "shadow"
        if deployment_profile is DeploymentProfile.LIVE_SHADOW:
            return "canary"
        if deployment_profile is DeploymentProfile.PAPER_RUNTIME_ENTRIES:
            return "runtime_entries"
        if deployment_profile is DeploymentProfile.LIVE_RUNTIME_ENTRIES:
            return "canary"
        if deployment_profile in {
            DeploymentProfile.PAPER_RUNTIME_PRIMARY,
            DeploymentProfile.LIVE_RUNTIME_PRIMARY,
        }:
            return "primary"

    if rollout_environment == "live":
        if state_source == ProjectionSource.LEGACY.value:
            return "legacy"
        if runtime_owns_entries or runtime_owns_exits:
            return "primary" if runtime_owns_exits else "canary"
        return "canary"

    if state_source == ProjectionSource.LEGACY.value:
        return "legacy"
    if runtime_owns_exits:
        return "primary"
    if runtime_owns_entries:
        return "runtime_entries"
    if runtime_mode == RuntimeMode.SHADOW.value:
        return "shadow"
    return "runtime"
