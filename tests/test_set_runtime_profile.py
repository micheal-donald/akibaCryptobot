from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from set_runtime_profile import (  # noqa: E402
    apply_deployment_profile,
    load_config,
    main,
    write_config,
)


def test_apply_deployment_profile_synchronizes_runtime_fields() -> None:
    updated_config, resolved_profile = apply_deployment_profile(
        {"runtime": {"runtime_mode": "legacy", "shadow_mode": False}},
        "paper_runtime_primary",
    )

    runtime = updated_config["runtime"]
    assert resolved_profile.value == "paper_runtime_primary"
    assert runtime["deployment_profile"] == "paper_runtime_primary"
    assert runtime["runtime_mode"] == "runtime_primary"
    assert runtime["shadow_mode"] is False


def test_apply_deployment_profile_creates_runtime_section() -> None:
    updated_config, resolved_profile = apply_deployment_profile(
        {},
        "paper_shadow",
    )

    assert resolved_profile.value == "paper_shadow"
    assert updated_config["runtime"] == {
        "deployment_profile": "paper_shadow",
        "runtime_mode": "shadow",
        "shadow_mode": True,
    }


def test_main_updates_config_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    original_config = {
        "exchange": {"sandbox": True},
        "runtime": {
            "deployment_profile": "paper_legacy",
            "runtime_mode": "legacy",
            "shadow_mode": False,
        },
    }
    write_config(config_path, original_config)

    exit_code = main(["paper_shadow", "--config", str(config_path)])

    assert exit_code == 0
    loaded = load_config(config_path)
    assert loaded["runtime"] == {
        "deployment_profile": "paper_shadow",
        "runtime_mode": "shadow",
        "shadow_mode": True,
    }

    output = capsys.readouterr().out
    assert "Updated" in output
    assert "paper_shadow" in output


def test_main_rejects_unsupported_profile(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"runtime": {}}), encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        main(["paper", "--config", str(config_path)])

    assert excinfo.value.code == 2
