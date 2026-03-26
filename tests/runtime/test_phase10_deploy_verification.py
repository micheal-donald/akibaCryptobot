from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "deploy_snapshot.sh"


def run_verify_status_payload(
    status_payload: dict[str, object],
    expected_profile: str = "",
    expected_effective_mode: str = "",
) -> subprocess.CompletedProcess[str]:
    command = (
        f'source "{SCRIPT_PATH}"; '
        'verify_status_payload "$STATUS_JSON" '
        f'"{expected_profile}" "{expected_effective_mode}"'
    )
    return subprocess.run(
        ["bash", "-lc", command],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "STATUS_JSON": json.dumps(status_payload)},
    )


def test_verify_status_payload_allows_unconstrained_deploys() -> None:
    result = run_verify_status_payload(
        {
            "configured_deployment_profile": "paper_shadow",
            "effective_runtime_mode": "shadow",
        }
    )

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("status_payload", "expected_profile", "expected_effective_mode", "message"),
    [
        (
            {
                "configured_deployment_profile": "paper_legacy",
                "effective_runtime_mode": "legacy",
            },
            "paper_shadow",
            "",
            "configured_deployment_profile mismatch",
        ),
        (
            {
                "configured_deployment_profile": "paper_shadow",
                "effective_runtime_mode": "shadow",
            },
            "",
            "runtime_primary",
            "effective_runtime_mode mismatch",
        ),
    ],
)
def test_verify_status_payload_fails_on_mismatch(
    status_payload: dict[str, object],
    expected_profile: str,
    expected_effective_mode: str,
    message: str,
) -> None:
    result = run_verify_status_payload(
        status_payload,
        expected_profile=expected_profile,
        expected_effective_mode=expected_effective_mode,
    )

    assert result.returncode == 1
    assert "Status verification failed:" in result.stderr
    assert message in result.stderr


def test_verify_status_payload_accepts_matching_expectations() -> None:
    result = run_verify_status_payload(
        {
            "configured_deployment_profile": "paper_runtime_primary",
            "effective_runtime_mode": "runtime_primary",
        },
        expected_profile="paper_runtime_primary",
        expected_effective_mode="runtime_primary",
    )

    assert result.returncode == 0
    assert result.stderr == ""
