from __future__ import annotations

import pytest

from runtime.config import RuntimeConfig
from runtime.mode import DeploymentProfile, RuntimeMode


@pytest.mark.parametrize(
    ("profile", "expected_mode", "expected_shadow"),
    [
        ("live_legacy", RuntimeMode.LEGACY, False),
        ("live_shadow", RuntimeMode.SHADOW, True),
        ("live_runtime_entries", RuntimeMode.RUNTIME_ENTRIES, False),
        ("live_runtime_primary", RuntimeMode.RUNTIME_PRIMARY, False),
    ],
)
def test_phase11_live_profiles_parse_to_expected_runtime_modes(
    profile: str,
    expected_mode: RuntimeMode,
    expected_shadow: bool,
) -> None:
    config = RuntimeConfig.from_mapping({"runtime": {"deployment_profile": profile}})

    assert config.deployment_profile is DeploymentProfile.parse(profile)
    assert config.runtime_mode is expected_mode
    assert config.shadow_mode is expected_shadow


def test_phase11_live_runtime_profiles_still_require_explicit_live_override() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "exchange": {"sandbox": False},
            "runtime": {"deployment_profile": "live_runtime_entries"},
        }
    )

    with pytest.raises(ValueError, match="allow_live_runtime"):
        config.validate_startup(exchange_sandbox=False)
