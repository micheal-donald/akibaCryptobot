"""Native shadow helpers for comparison-only hot-path validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from ports.native_hot_path import NativeHotPathPorts
from runtime.ipc_protocol import (
    IPCEnvelope,
    IPCPayloadKind,
    IPCProtocolError,
    decode_ipc_envelope,
)
from runtime.view_models import RuntimeProjection


@dataclass(slots=True, frozen=True)
class NativeShadowSnapshot:
    """Comparable runtime snapshot for shadow-mode validation."""

    runtime_mode: str
    runtime_is_running: bool
    runtime_owns_entries: bool
    runtime_owns_exits: bool
    active_position_count: int
    open_order_count: int
    state: dict[str, object]


@dataclass(slots=True, frozen=True)
class NativeShadowComparison:
    """Comparison result between Python runtime state and native shadow state."""

    python_snapshot: NativeShadowSnapshot
    native_snapshot: NativeShadowSnapshot | None
    mismatches: tuple[str, ...]

    @property
    def is_match(self) -> bool:
        return not self.mismatches


@dataclass(slots=True)
class NativeShadowBridge:
    """Read-only comparison bridge for optional native shadow responses."""

    native_ports: NativeHotPathPorts | None = None

    def is_enabled(self) -> bool:
        return self.native_ports is not None and (
            self.native_ports.market_data is not None
            or self.native_ports.execution is not None
        )

    def snapshot_from_projection(
        self,
        projection: RuntimeProjection,
    ) -> NativeShadowSnapshot:
        return NativeShadowSnapshot(
            runtime_mode=projection.runtime_mode,
            runtime_is_running=projection.runtime_is_running,
            runtime_owns_entries=projection.runtime_owns_entries,
            runtime_owns_exits=projection.runtime_owns_exits,
            active_position_count=projection.active_position_count,
            open_order_count=projection.open_order_count,
            state=_projection_state(projection),
        )

    def snapshot_from_ipc(self, raw: str | bytes) -> NativeShadowSnapshot:
        envelope = decode_ipc_envelope(raw)
        return self.snapshot_from_envelope(envelope)

    def snapshot_from_envelope(self, envelope: IPCEnvelope) -> NativeShadowSnapshot:
        if envelope.kind is not IPCPayloadKind.MARKET_DATA:
            raise IPCProtocolError(
                f"native shadow expects market_data envelopes, got {envelope.kind.value!r}"
            )
        payload = envelope.payload
        if not isinstance(payload, Mapping):
            raise IPCProtocolError("native shadow payload must be a mapping")
        runtime_mode = str(payload.get("runtime_mode", "native_shadow"))
        runtime_is_running = bool(payload.get("runtime_is_running", False))
        runtime_owns_entries = bool(payload.get("runtime_owns_entries", False))
        runtime_owns_exits = bool(payload.get("runtime_owns_exits", False))
        active_position_count = int(payload.get("active_position_count", 0))
        open_order_count = int(payload.get("open_order_count", 0))
        return NativeShadowSnapshot(
            runtime_mode=runtime_mode,
            runtime_is_running=runtime_is_running,
            runtime_owns_entries=runtime_owns_entries,
            runtime_owns_exits=runtime_owns_exits,
            active_position_count=active_position_count,
            open_order_count=open_order_count,
            state=_normalize_mapping(payload),
        )

    def compare(
        self,
        projection: RuntimeProjection,
        native_snapshot: NativeShadowSnapshot | None,
    ) -> NativeShadowComparison:
        python_snapshot = self.snapshot_from_projection(projection)
        if native_snapshot is None:
            return NativeShadowComparison(
                python_snapshot=python_snapshot,
                native_snapshot=None,
                mismatches=("native_snapshot_missing",),
            )

        mismatches = []
        if native_snapshot.runtime_mode != python_snapshot.runtime_mode:
            mismatches.append("runtime_mode")
        if native_snapshot.runtime_is_running != python_snapshot.runtime_is_running:
            mismatches.append("runtime_is_running")
        if native_snapshot.runtime_owns_entries != python_snapshot.runtime_owns_entries:
            mismatches.append("runtime_owns_entries")
        if native_snapshot.runtime_owns_exits != python_snapshot.runtime_owns_exits:
            mismatches.append("runtime_owns_exits")
        if (
            native_snapshot.active_position_count
            != python_snapshot.active_position_count
        ):
            mismatches.append("active_position_count")
        if native_snapshot.open_order_count != python_snapshot.open_order_count:
            mismatches.append("open_order_count")

        return NativeShadowComparison(
            python_snapshot=python_snapshot,
            native_snapshot=native_snapshot,
            mismatches=tuple(mismatches),
        )


def _projection_state(projection: RuntimeProjection) -> dict[str, object]:
    return {
        "runtime_mode": projection.runtime_mode,
        "runtime_enabled": projection.runtime_enabled,
        "runtime_is_running": projection.runtime_is_running,
        "runtime_is_healthy": projection.runtime_is_healthy,
        "runtime_owns_entries": projection.runtime_owns_entries,
        "runtime_owns_exits": projection.runtime_owns_exits,
        "active_position_count": projection.active_position_count,
        "open_order_count": projection.open_order_count,
        "rejected_order_count": projection.rejected_order_count,
    }


def _normalize_mapping(value: Mapping[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(item, Mapping):
            normalized[str(key)] = _normalize_mapping(item)
        elif isinstance(item, tuple):
            normalized[str(key)] = [_normalize_value(child) for child in item]
        else:
            normalized[str(key)] = _normalize_value(item)
    return normalized


def _normalize_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return _normalize_mapping(value)
    if isinstance(value, tuple):
        return [_normalize_value(item) for item in value]
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    return value
