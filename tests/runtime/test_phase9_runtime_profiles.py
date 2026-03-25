from __future__ import annotations

import pytest

from runtime.config import RuntimeConfig
from runtime.mode import DeploymentProfile, RuntimeMode


@pytest.mark.parametrize(
    ("profile", "expected_mode", "expected_shadow_mode"),
    [
        (DeploymentProfile.PAPER_LEGACY, RuntimeMode.LEGACY, False),
        (DeploymentProfile.PAPER_SHADOW, RuntimeMode.SHADOW, True),
        (
            DeploymentProfile.PAPER_RUNTIME_ENTRIES,
            RuntimeMode.RUNTIME_ENTRIES,
            False,
        ),
        (
            DeploymentProfile.PAPER_RUNTIME_PRIMARY,
            RuntimeMode.RUNTIME_PRIMARY,
            False,
        ),
        (DeploymentProfile.LIVE_LEGACY, RuntimeMode.LEGACY, False),
    ],
)
def test_phase9_profiles_resolve_to_effective_runtime_modes(
    profile: DeploymentProfile,
    expected_mode: RuntimeMode,
    expected_shadow_mode: bool,
) -> None:
    config = RuntimeConfig.from_mapping(
        {
            "runtime": {
                "deployment_profile": profile.value,
            }
        }
    )

    assert config.deployment_profile is profile
    assert config.runtime_mode is expected_mode
    assert config.shadow_mode is expected_shadow_mode


def test_phase9_profile_allows_matching_compatibility_inputs() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "runtime": {
                "deployment_profile": "paper_runtime_entries",
                "runtime_mode": "runtime_entries",
                "shadow_mode": False,
            }
        }
    )

    assert config.deployment_profile is DeploymentProfile.PAPER_RUNTIME_ENTRIES
    assert config.runtime_mode is RuntimeMode.RUNTIME_ENTRIES
    assert config.shadow_mode is False


@pytest.mark.parametrize(
    ("runtime_mode", "shadow_mode", "match"),
    [
        ("runtime_primary", False, "runtime_mode"),
        ("legacy", True, "shadow_mode"),
    ],
)
def test_phase9_profile_rejects_contradictory_inputs(
    runtime_mode: str,
    shadow_mode: bool,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        RuntimeConfig.from_mapping(
            {
                "runtime": {
                    "deployment_profile": "paper_legacy",
                    "runtime_mode": runtime_mode,
                    "shadow_mode": shadow_mode,
                }
            }
        )


def test_phase9_profile_rejects_invalid_profile_names() -> None:
    with pytest.raises(ValueError, match="unsupported deployment profile"):
        RuntimeConfig.from_mapping(
            {
                "runtime": {
                    "deployment_profile": "paper",
                }
            }
        )
