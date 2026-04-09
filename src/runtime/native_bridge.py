"""Native shadow canary bridge and operator-facing report helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from runtime.ipc_protocol import IPCEnvelope, IPCProtocolError, decode_ipc_envelope
from runtime.native_shadow import NativeShadowBridge, NativeShadowSnapshot

DEFAULT_NATIVE_SHADOW_STALE_SECONDS = 30.0


@dataclass(slots=True)
class NativeShadowCanaryBridge:
    """Ingest and compare native shadow snapshots for the rollout canary."""

    stale_after_seconds: float = DEFAULT_NATIVE_SHADOW_STALE_SECONDS

    def ingest_snapshot(
        self,
        raw: str | bytes | Mapping[str, Any] | IPCEnvelope,
        *,
        runtime_app: object | None = None,
    ) -> NativeShadowSnapshot:
        envelope = _coerce_envelope(raw)
        snapshot = NativeShadowBridge().snapshot_from_envelope(envelope)
        if runtime_app is not None:
            setattr(runtime_app, "native_shadow_snapshot", snapshot)
        return snapshot

    def build_report(
        self,
        bot: object,
        *,
        projection: object | None = None,
        now: datetime | None = None,
    ) -> dict[str, object] | None:
        runtime_app = getattr(bot, "runtime_app", None)
        if runtime_app is None:
            return None
        native_ports = getattr(runtime_app, "native_hot_path_ports", None)
        bridge = NativeShadowBridge(native_ports=native_ports)
        if not bridge.is_enabled():
            return None

        if projection is None:
            projection_getter = getattr(bot, "get_runtime_projection", None)
            if not callable(projection_getter):
                return None
            projection = projection_getter()
        if projection is None:
            return None

        snapshot = getattr(runtime_app, "native_shadow_snapshot", None)
        if snapshot is None:
            return _build_native_shadow_report(
                status="awaiting_native_snapshot",
                is_match=False,
                mismatches=("native_snapshot_missing",),
                summary="Native shadow is enabled; awaiting the first native snapshot.",
                recommended_action="wait_for_native_snapshot",
                last_seen_at=None,
                age_seconds=None,
                stale_after_seconds=self.stale_after_seconds,
            )

        age_seconds = snapshot.age_seconds(now=now or datetime.now(timezone.utc))
        if snapshot.is_stale(self.stale_after_seconds, now=now):
            return _build_native_shadow_report(
                status="stale_native_snapshot",
                is_match=False,
                mismatches=("native_snapshot_stale",),
                summary=(
                    "Native shadow snapshot is stale; refresh the native bridge before "
                    "promotion."
                ),
                recommended_action="refresh_native_snapshot",
                last_seen_at=snapshot.captured_at.isoformat()
                if snapshot.captured_at is not None
                else None,
                age_seconds=age_seconds,
                stale_after_seconds=self.stale_after_seconds,
            )

        comparison = bridge.compare(
            projection,
            snapshot,
        )
        return _build_native_shadow_report(
            status="match" if comparison.is_match else "divergent",
            is_match=comparison.is_match,
            mismatches=comparison.mismatches,
            summary=(
                "Native shadow matches Python runtime."
                if comparison.is_match
                else "Native shadow diverges from Python runtime."
            ),
            recommended_action=(
                "continue_native_shadow"
                if comparison.is_match
                else "investigate_native_divergence"
            ),
            last_seen_at=snapshot.captured_at.isoformat()
            if snapshot.captured_at is not None
            else None,
            age_seconds=age_seconds,
            stale_after_seconds=self.stale_after_seconds,
        )


def build_native_shadow_report(
    bot: object,
    *,
    projection: object | None = None,
    now: datetime | None = None,
    stale_after_seconds: float = DEFAULT_NATIVE_SHADOW_STALE_SECONDS,
) -> dict[str, object] | None:
    """Build a canary report for operator-facing native shadow surfaces."""

    return NativeShadowCanaryBridge(
        stale_after_seconds=stale_after_seconds,
    ).build_report(bot, projection=projection, now=now)


def ingest_native_shadow_snapshot(
    raw: str | bytes | Mapping[str, Any] | IPCEnvelope,
    *,
    runtime_app: object | None = None,
) -> NativeShadowSnapshot:
    """Decode and optionally attach a native shadow snapshot to the runtime app."""

    return NativeShadowCanaryBridge().ingest_snapshot(
        raw,
        runtime_app=runtime_app,
    )


def _coerce_envelope(
    raw: str | bytes | Mapping[str, Any] | IPCEnvelope,
) -> IPCEnvelope:
    if isinstance(raw, IPCEnvelope):
        return raw
    if isinstance(raw, Mapping):
        return IPCEnvelope.from_mapping(raw)
    try:
        return decode_ipc_envelope(raw)
    except IPCProtocolError:
        raise


def _build_native_shadow_report(
    *,
    status: str,
    is_match: bool,
    mismatches: tuple[str, ...],
    summary: str,
    recommended_action: str,
    last_seen_at: str | None,
    age_seconds: float | None,
    stale_after_seconds: float | None = None,
) -> dict[str, object]:
    return {
        "native_shadow_enabled": True,
        "native_shadow_status": status,
        "native_shadow_is_match": is_match,
        "native_shadow_mismatch_count": len(mismatches),
        "native_shadow_mismatches": mismatches,
        "native_shadow_summary": summary,
        "native_shadow_recommended_action": recommended_action,
        "native_shadow_last_seen_at": last_seen_at,
        "native_shadow_age_seconds": age_seconds,
        "native_shadow_stale_after_seconds": stale_after_seconds,
    }
