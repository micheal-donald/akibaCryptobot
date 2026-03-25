#!/usr/bin/env python3
"""Apply a reviewed deployment profile to `config/config.json` locally."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from runtime.mode import DeploymentProfile  # noqa: E402


DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "config.json"


def load_config(path: Path) -> dict[str, Any]:
    """Load a JSON config file into a mutable mapping."""

    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise TypeError("config file must contain a JSON object")
    return data


def write_config(path: Path, config: dict[str, Any]) -> None:
    """Write a JSON config file with stable formatting."""

    text = json.dumps(config, indent=2)
    path.write_text(f"{text}\n", encoding="utf-8")


def apply_deployment_profile(
    config: dict[str, Any],
    profile: DeploymentProfile | str,
) -> tuple[dict[str, Any], DeploymentProfile]:
    """Return a copy of config with runtime compatibility fields synchronized."""

    resolved_profile = DeploymentProfile.parse(profile)
    updated_config = copy.deepcopy(config)
    runtime_section = updated_config.setdefault("runtime", {})
    if not isinstance(runtime_section, dict):
        raise TypeError("runtime section must be a JSON object")

    runtime_section["deployment_profile"] = resolved_profile.value
    runtime_section["runtime_mode"] = resolved_profile.runtime_mode.value
    runtime_section["shadow_mode"] = resolved_profile.shadow_mode
    return updated_config, resolved_profile


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Apply a reviewed deployment profile to a local config/config.json "
            "file while keeping runtime compatibility fields synchronized."
        )
    )
    parser.add_argument(
        "profile",
        help=(
            "Deployment profile to apply: paper_legacy, paper_shadow, "
            "paper_runtime_entries, paper_runtime_primary, or live_legacy"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to config/config.json (default: {DEFAULT_CONFIG_PATH})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        updated_config, resolved_profile = apply_deployment_profile(
            config,
            args.profile,
        )
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))

    if updated_config != config:
        write_config(args.config, updated_config)
        print(f"Updated {args.config}")
    else:
        print(f"No changes needed for {args.config}")

    runtime_section = updated_config.get("runtime", {})
    print(
        "deployment_profile="
        f"{runtime_section.get('deployment_profile')}"
        f" runtime_mode={runtime_section.get('runtime_mode')}"
        f" shadow_mode={runtime_section.get('shadow_mode')}"
        f" profile={resolved_profile.value}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
