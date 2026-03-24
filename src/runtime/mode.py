"""Runtime ownership modes and helper predicates."""

from __future__ import annotations

from enum import StrEnum


class RuntimeMode(StrEnum):
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
    "RuntimeMode",
    "runtime_enabled",
    "runtime_is_shadow",
    "runtime_owns_entries",
    "runtime_owns_exits",
]
