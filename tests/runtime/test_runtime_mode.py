from __future__ import annotations

import pytest

from runtime.mode import (
    RuntimeMode,
    runtime_enabled,
    runtime_is_shadow,
    runtime_owns_entries,
    runtime_owns_exits,
)


@pytest.mark.parametrize(
    ("mode", "enabled", "owns_entries", "owns_exits", "shadow_only"),
    [
        (RuntimeMode.LEGACY, False, False, False, False),
        (RuntimeMode.SHADOW, True, False, False, True),
        (RuntimeMode.RUNTIME_ENTRIES, True, True, False, False),
        (RuntimeMode.RUNTIME_PRIMARY, True, True, True, False),
    ],
)
def test_runtime_mode_helpers_cover_all_modes(
    mode: RuntimeMode,
    enabled: bool,
    owns_entries: bool,
    owns_exits: bool,
    shadow_only: bool,
) -> None:
    assert runtime_enabled(mode) is enabled
    assert runtime_owns_entries(mode) is owns_entries
    assert runtime_owns_exits(mode) is owns_exits
    assert runtime_is_shadow(mode) is shadow_only


def test_runtime_mode_parse_accepts_strings() -> None:
    assert RuntimeMode.parse("runtime_primary") is RuntimeMode.RUNTIME_PRIMARY


def test_runtime_mode_parse_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="unsupported runtime mode"):
        RuntimeMode.parse("paper")
