from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ports.native_hot_path import NativeHotPathPorts
from runtime.app import RuntimeApp
from runtime.config import RuntimeConfig
from runtime.ipc_protocol import IPCEnvelope, IPCPayloadKind
from runtime.native_shadow import NativeShadowBridge
from runtime.projection import build_runtime_projection
from runtime.view_models import RuntimeProjection


@dataclass(slots=True)
class _ProjectionSourceStub:
    def get_all_position_states(self) -> tuple[object, ...]:
        return ()

    def get_all_order_states(self) -> tuple[object, ...]:
        return ()


def _projection() -> RuntimeProjection:
    return build_runtime_projection(
        runtime_mode="shadow",
        runtime_is_running=True,
        position_source=_ProjectionSourceStub(),
        execution_source=_ProjectionSourceStub(),
    )


def _runtime_config() -> RuntimeConfig:
    return RuntimeConfig.from_mapping(
        {
            "runtime": {
                "deployment_profile": "paper_runtime_primary",
            },
            "trading": {
                "pairs": ["BTC/USDT"],
                "timeframes": {"primary": "1m"},
            },
            "exchange": {"sandbox": True},
        }
    )


def test_native_shadow_bridge_reports_disabled_without_ports() -> None:
    bridge = NativeShadowBridge()

    assert bridge.is_enabled() is False


def test_native_shadow_bridge_compares_python_and_native_snapshots() -> None:
    bridge = NativeShadowBridge(native_ports=NativeHotPathPorts())
    projection = _projection()

    native = bridge.snapshot_from_envelope(
        IPCEnvelope.create(
            IPCPayloadKind.MARKET_DATA,
            {
                "runtime_mode": "shadow",
                "runtime_is_running": True,
                "runtime_owns_entries": False,
                "runtime_owns_exits": False,
                "active_position_count": 0,
                "open_order_count": 0,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    )

    comparison = bridge.compare(projection, native)

    assert comparison.is_match is True
    assert comparison.mismatches == ()
    assert comparison.python_snapshot.runtime_mode == "shadow"
    assert comparison.native_snapshot is not None


def test_native_shadow_bridge_reports_mismatches() -> None:
    bridge = NativeShadowBridge(native_ports=NativeHotPathPorts())
    projection = _projection()
    native = bridge.snapshot_from_envelope(
        IPCEnvelope.create(
            IPCPayloadKind.MARKET_DATA,
            {
                "runtime_mode": "primary",
                "runtime_is_running": False,
                "runtime_owns_entries": True,
                "runtime_owns_exits": True,
                "active_position_count": 2,
                "open_order_count": 1,
            },
        )
    )

    comparison = bridge.compare(projection, native)

    assert comparison.is_match is False
    assert set(comparison.mismatches) == {
        "runtime_mode",
        "runtime_is_running",
        "runtime_owns_entries",
        "runtime_owns_exits",
        "active_position_count",
        "open_order_count",
    }


def test_runtime_app_native_shadow_report_is_benign_without_snapshot() -> None:
    native_ports = NativeHotPathPorts(market_data=object())
    app = RuntimeApp.build(
        _runtime_config(),
        native_hot_path_ports=native_ports,
    )
    app.is_running = True

    report = app.get_native_shadow_report()

    assert report is not None
    assert report["native_shadow_status"] == "awaiting_native_snapshot"
    assert report["native_shadow_is_match"] is True
    assert report["native_shadow_mismatch_count"] == 0
    assert report["native_shadow_mismatches"] == ()


def test_runtime_app_native_shadow_report_is_disabled_without_native_ports() -> None:
    app = RuntimeApp.build(_runtime_config())
    app.is_running = True

    report = app.get_native_shadow_report()

    assert report is None


def test_runtime_app_native_shadow_report_surfaces_divergence() -> None:
    native_ports = NativeHotPathPorts(market_data=object())
    bridge = NativeShadowBridge(native_ports=native_ports)
    app = RuntimeApp.build(
        _runtime_config(),
        native_hot_path_ports=native_ports,
    )
    app.is_running = True
    app.native_shadow_snapshot = bridge.snapshot_from_envelope(
        IPCEnvelope.create(
            IPCPayloadKind.MARKET_DATA,
            {
                "runtime_mode": "runtime_primary",
                "runtime_is_running": True,
                "runtime_owns_entries": True,
                "runtime_owns_exits": True,
                "active_position_count": 2,
                "open_order_count": 1,
            },
        )
    )

    report = app.get_native_shadow_report()

    assert report is not None
    assert report["native_shadow_status"] == "divergent"
    assert report["native_shadow_is_match"] is False
    assert report["native_shadow_mismatch_count"] == 2
    assert report["native_shadow_mismatches"] == (
        "active_position_count",
        "open_order_count",
    )
