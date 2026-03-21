"""Runtime configuration objects for the phase 1 runtime shell."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
import json


@dataclass(slots=True, frozen=True)
class RuntimeConfig:
    """Typed runtime configuration used by the new event-driven shell."""

    symbols: tuple[str, ...]
    kline_intervals: tuple[str, ...]
    candle_history_limit: int = 2048
    event_bus_maxsize: int = 0
    housekeeping_interval_seconds: float = 60.0
    shadow_mode: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "RuntimeConfig":
        """Create a runtime config from the existing nested config structure."""

        runtime_section = data.get("runtime", {})
        trading_section = data.get("trading", {})

        symbols = cls._as_string_tuple(trading_section.get("pairs", ()))
        intervals = cls._as_string_tuple(
            runtime_section.get("kline_intervals", ("1m",))
        )

        return cls(
            symbols=symbols,
            kline_intervals=intervals,
            candle_history_limit=int(runtime_section.get("candle_history_limit", 2048)),
            event_bus_maxsize=int(runtime_section.get("event_bus_maxsize", 0)),
            housekeeping_interval_seconds=float(
                runtime_section.get("housekeeping_interval_seconds", 60.0)
            ),
            shadow_mode=bool(runtime_section.get("shadow_mode", True)),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "RuntimeConfig":
        """Load runtime config from a JSON configuration file."""

        with Path(path).open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, Mapping):
            raise TypeError("runtime config file must contain a JSON object")
        return cls.from_mapping(data)

    @staticmethod
    def _as_string_tuple(values: Sequence[Any] | Any) -> tuple[str, ...]:
        if isinstance(values, str):
            return (values,)
        if isinstance(values, Sequence):
            return tuple(str(value) for value in values)
        return ()
