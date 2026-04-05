"""Runtime ownership modes and helper predicates."""

from __future__ import annotations

from enum import Enum


class DeploymentProfile(str, Enum):
    """Reviewed deployment profiles for runtime rollout selection."""

    PAPER_LEGACY = "paper_legacy"
    PAPER_SHADOW = "paper_shadow"
    PAPER_RUNTIME_ENTRIES = "paper_runtime_entries"
    PAPER_RUNTIME_PRIMARY = "paper_runtime_primary"
    LIVE_LEGACY = "live_legacy"
    LIVE_SHADOW = "live_shadow"
    LIVE_RUNTIME_ENTRIES = "live_runtime_entries"
    LIVE_RUNTIME_PRIMARY = "live_runtime_primary"

    @classmethod
    def parse(cls, value: "DeploymentProfile | str") -> "DeploymentProfile":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(f"unsupported deployment profile: {value!r}") from exc

    @property
    def runtime_mode(self) -> "RuntimeMode":
        return {
            DeploymentProfile.PAPER_LEGACY: RuntimeMode.LEGACY,
            DeploymentProfile.PAPER_SHADOW: RuntimeMode.SHADOW,
            DeploymentProfile.PAPER_RUNTIME_ENTRIES: RuntimeMode.RUNTIME_ENTRIES,
            DeploymentProfile.PAPER_RUNTIME_PRIMARY: RuntimeMode.RUNTIME_PRIMARY,
            DeploymentProfile.LIVE_LEGACY: RuntimeMode.LEGACY,
            DeploymentProfile.LIVE_SHADOW: RuntimeMode.SHADOW,
            DeploymentProfile.LIVE_RUNTIME_ENTRIES: RuntimeMode.RUNTIME_ENTRIES,
            DeploymentProfile.LIVE_RUNTIME_PRIMARY: RuntimeMode.RUNTIME_PRIMARY,
        }[self]

    @property
    def shadow_mode(self) -> bool:
        return self in {DeploymentProfile.PAPER_SHADOW, DeploymentProfile.LIVE_SHADOW}

    @property
    def is_live(self) -> bool:
        return self in {
            DeploymentProfile.LIVE_LEGACY,
            DeploymentProfile.LIVE_SHADOW,
            DeploymentProfile.LIVE_RUNTIME_ENTRIES,
            DeploymentProfile.LIVE_RUNTIME_PRIMARY,
        }

    @property
    def is_canary(self) -> bool:
        return self in {
            DeploymentProfile.LIVE_SHADOW,
            DeploymentProfile.LIVE_RUNTIME_ENTRIES,
        }


class RuntimeMode(str, Enum):
    """Defines which runtime path owns trading decisions."""

    LEGACY = "legacy"
    SHADOW = "shadow"
    RUNTIME_ENTRIES = "runtime_entries"
    RUNTIME_PRIMARY = "runtime_primary"

    @classmethod
    def parse(cls, value: "RuntimeMode | str") -> "RuntimeMode":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(f"unsupported runtime mode: {value!r}") from exc

    @property
    def runtime_enabled(self) -> bool:
        return self is not RuntimeMode.LEGACY

    @property
    def shadow_only(self) -> bool:
        return self is RuntimeMode.SHADOW

    @property
    def owns_entries(self) -> bool:
        return self in {RuntimeMode.RUNTIME_ENTRIES, RuntimeMode.RUNTIME_PRIMARY}

    @property
    def owns_exits(self) -> bool:
        return self is RuntimeMode.RUNTIME_PRIMARY


def runtime_enabled(mode: RuntimeMode | str) -> bool:
    return RuntimeMode.parse(mode).runtime_enabled


def runtime_owns_entries(mode: RuntimeMode | str) -> bool:
    return RuntimeMode.parse(mode).owns_entries


def runtime_owns_exits(mode: RuntimeMode | str) -> bool:
    return RuntimeMode.parse(mode).owns_exits


def runtime_is_shadow(mode: RuntimeMode | str) -> bool:
    return RuntimeMode.parse(mode).shadow_only


__all__ = [
    "DeploymentProfile",
    "RuntimeMode",
    "runtime_enabled",
    "runtime_is_shadow",
    "runtime_owns_entries",
    "runtime_owns_exits",
]
