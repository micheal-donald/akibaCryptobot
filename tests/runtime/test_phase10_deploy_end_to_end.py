from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "deploy_snapshot.sh"


def _write_executable_script(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _build_fake_toolchain(
    tmp_path: Path,
    *,
    configured_profile: str,
    effective_mode: str,
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    _write_executable_script(
        bin_dir / "ssh",
        """#!/usr/bin/env bash
set -euo pipefail

tmp_script="$(mktemp)"
cat >"$tmp_script"
bash "$tmp_script"
status=$?
rm -f "$tmp_script"
exit "$status"
""",
    )

    _write_executable_script(
        bin_dir / "curl",
        """#!/usr/bin/env bash
set -euo pipefail

output_file=""
url=""

while (($#)); do
  case "$1" in
    -o)
      output_file="$2"
      shift 2
      ;;
    -fsS|-f|-s|-S)
      shift
      ;;
    --*)
      shift
      ;;
    *)
      if [[ -z "$url" ]]; then
        url="$1"
      fi
      shift
      ;;
  esac
done

if [[ "$url" == *"/api/health" ]]; then
  payload='{"ok":true,"service":"akibabot"}'
elif [[ "$url" == *"/api/status" ]]; then
  payload=$(cat <<EOF
{"configured_deployment_profile":"${FAKE_STATUS_CONFIGURED_PROFILE}","effective_runtime_mode":"${FAKE_STATUS_EFFECTIVE_MODE}"}
EOF
)
else
  echo "unexpected URL: $url" >&2
  exit 1
fi

if [[ -n "$output_file" ]]; then
  printf '%s\n' "$payload" >"$output_file"
else
  printf '%s\n' "$payload"
fi
""",
    )

    _write_executable_script(
        bin_dir / "systemctl",
        """#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  restart|is-active|status)
    exit 0
    ;;
  *)
    echo "unexpected systemctl invocation: $*" >&2
    exit 1
    ;;
esac
""",
    )

    _write_executable_script(
        bin_dir / "journalctl",
        """#!/usr/bin/env bash
set -euo pipefail
exit 0
""",
    )

    _write_executable_script(
        bin_dir / "rsync",
        """#!/usr/bin/env bash
set -euo pipefail
exit 0
""",
    )

    return bin_dir


def _run_deploy(
    tmp_path: Path,
    *,
    expected_profile: str,
    expected_effective_mode: str,
    configured_profile: str,
    effective_mode: str,
) -> subprocess.CompletedProcess[str]:
    bin_dir = _build_fake_toolchain(
        tmp_path,
        configured_profile=configured_profile,
        effective_mode=effective_mode,
    )

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "DEPLOY_SHA": subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            cwd=SCRIPT_PATH.parents[1],
        ).strip(),
        "REMOTE_APP_DIR": str(tmp_path / "remote-app"),
        "REMOTE_SERVICE": "akibabot.service",
        "REMOTE_PORT": "8000",
        "REMOTE_HOST": "fake-host",
        "REMOTE_USER": "fake-user",
        "SSH_KEY": str(tmp_path / "fake-key"),
        "EXPECTED_CONFIGURED_DEPLOYMENT_PROFILE": expected_profile,
        "EXPECTED_EFFECTIVE_RUNTIME_MODE": expected_effective_mode,
        "FAKE_STATUS_CONFIGURED_PROFILE": configured_profile,
        "FAKE_STATUS_EFFECTIVE_MODE": effective_mode,
    }

    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=SCRIPT_PATH.parents[1],
    )


def test_deploy_snapshot_verifies_successful_profile_and_mode(
    tmp_path: Path,
) -> None:
    deploy_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        text=True,
        cwd=SCRIPT_PATH.parents[1],
    ).strip()
    result = _run_deploy(
        tmp_path,
        expected_profile="paper_runtime_primary",
        expected_effective_mode="runtime_primary",
        configured_profile="paper_runtime_primary",
        effective_mode="runtime_primary",
    )

    assert result.returncode == 0
    assert f"Deploy SHA: {deploy_sha}" in result.stdout
    assert "--- HEALTH ---" in result.stdout
    assert "--- STATUS ---" in result.stdout
    assert '"configured_deployment_profile":"paper_runtime_primary"' in result.stdout
    assert '"effective_runtime_mode":"runtime_primary"' in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize(
    (
        "expected_profile",
        "expected_mode",
        "configured_profile",
        "effective_mode",
        "message",
    ),
    [
        (
            "paper_runtime_primary",
            "runtime_primary",
            "paper_shadow",
            "runtime_primary",
            "configured_deployment_profile mismatch",
        ),
        (
            "paper_runtime_primary",
            "runtime_primary",
            "paper_runtime_primary",
            "shadow",
            "effective_runtime_mode mismatch",
        ),
    ],
)
def test_deploy_snapshot_fails_on_profile_or_mode_mismatch(
    tmp_path: Path,
    expected_profile: str,
    expected_mode: str,
    configured_profile: str,
    effective_mode: str,
    message: str,
) -> None:
    result = _run_deploy(
        tmp_path,
        expected_profile=expected_profile,
        expected_effective_mode=expected_mode,
        configured_profile=configured_profile,
        effective_mode=effective_mode,
    )

    assert result.returncode == 1
    assert "Status verification failed:" in result.stderr
    assert message in result.stderr
    assert "--- STATUS ---" not in result.stdout
