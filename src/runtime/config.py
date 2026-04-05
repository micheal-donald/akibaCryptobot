"""Runtime configuration objects for the runtime shell."""

from __future__ import annotations

import json
from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .mode import DeploymentProfile, RuntimeMode


@dataclass(slots=True, frozen=True)
class RuntimeConfig:
    """Typed runtime configuration used by the new event-driven shell."""

    symbols: tuple[str, ...]
    kline_intervals: tuple[str, ...]
    candle_history_limit: int = 2048
    queue_maxsize: int = 0
    event_bus_maxsize: int = 0
    stale_after_seconds: float = 30.0
    housekeeping_interval_seconds: float = 60.0
    market_data_source: str = "binance"
    sandbox_execution: bool = False
    sandbox_market_data: bool = False
    allow_live_runtime: bool | str = False
    deployment_profile: DeploymentProfile | str | None = None
    runtime_mode: RuntimeMode | str | None = None
    shadow_mode: bool | None = None

    def __post_init__(self) -> None:
        if self.candle_history_limit <= 0:
            raise ValueError("candle_history_limit must be positive")
        if self.queue_maxsize < 0:
            raise ValueError("queue_maxsize cannot be negative")
        if self.event_bus_maxsize < 0:
            raise ValueError("event_bus_maxsize cannot be negative")
        if self.stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        if self.housekeeping_interval_seconds <= 0:
            raise ValueError("housekeeping_interval_seconds must be positive")

        if self.queue_maxsize == 0 and self.event_bus_maxsize > 0:
            object.__setattr__(self, "queue_maxsize", self.event_bus_maxsize)
        elif self.event_bus_maxsize == 0 and self.queue_maxsize > 0:
            object.__setattr__(self, "event_bus_maxsize", self.queue_maxsize)
        elif self.queue_maxsize != self.event_bus_maxsize:
            raise ValueError(
                "queue_maxsize and event_bus_maxsize must match when both are set"
            )

        normalized_market_data_source = self.market_data_source.strip()
        if not normalized_market_data_source:
            raise ValueError("market_data_source must not be empty")
        object.__setattr__(self, "market_data_source", normalized_market_data_source)
        object.__setattr__(
            self,
            "allow_live_runtime",
            self._coerce_bool(self.allow_live_runtime, default=False),
        )

        deployment_profile = self._resolve_deployment_profile(
            self.deployment_profile,
        )
        if deployment_profile is not None:
            expected_runtime_mode = deployment_profile.runtime_mode
            expected_shadow_mode = deployment_profile.shadow_mode

            if self.runtime_mode is not None:
                configured_runtime_mode = RuntimeMode.parse(self.runtime_mode)
                if configured_runtime_mode is not expected_runtime_mode:
                    raise ValueError(
                        "deployment_profile "
                        f"{deployment_profile.value!r} conflicts with runtime_mode "
                        f"{configured_runtime_mode.value!r}"
                    )

            if self.shadow_mode is not None:
                configured_shadow_mode = self._coerce_bool(
                    self.shadow_mode,
                    default=expected_shadow_mode,
                )
                if configured_shadow_mode is not expected_shadow_mode:
                    raise ValueError(
                        "deployment_profile "
                        f"{deployment_profile.value!r} conflicts with shadow_mode "
                        f"{configured_shadow_mode!r}"
                    )

            object.__setattr__(self, "deployment_profile", deployment_profile)
            object.__setattr__(self, "runtime_mode", expected_runtime_mode)
            object.__setattr__(self, "shadow_mode", expected_shadow_mode)
        else:
            resolved_runtime_mode = self._resolve_runtime_mode(
                self.runtime_mode,
                shadow_mode=self.shadow_mode,
            )
            object.__setattr__(self, "deployment_profile", None)
            object.__setattr__(self, "runtime_mode", resolved_runtime_mode)
            object.__setattr__(self, "shadow_mode", resolved_runtime_mode.shadow_only)

    @property
    def deployment_profile_name(self) -> str | None:
        """Return the reviewed deployment profile name when one is configured."""

        if self.deployment_profile is None:
            return None
        return self.deployment_profile.value

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "RuntimeConfig":
        """Create a runtime config from the existing nested config structure.

        `runtime_mode` is the explicit rollout contract when present. `shadow_mode`
        remains as compatibility fallback for older configs that have not yet set a
        runtime mode.
        """

        runtime_section = cls._as_mapping(data.get("runtime"))
        trading_section = cls._as_mapping(data.get("trading"))
        exchange_section = cls._as_mapping(data.get("exchange"))

        symbols = cls._resolve_symbols(runtime_section, trading_section)
        intervals = cls._resolve_intervals(runtime_section, trading_section)
        queue_maxsize = cls._coerce_int(
            runtime_section.get("queue_maxsize"),
            runtime_section.get("event_bus_maxsize"),
            default=0,
        )
        event_bus_maxsize = cls._coerce_int(
            runtime_section.get("event_bus_maxsize"),
            runtime_section.get("queue_maxsize"),
            default=queue_maxsize,
        )
        market_data_source = cls._resolve_market_data_source(
            runtime_section,
            exchange_section,
        )

        return cls(
            symbols=symbols,
            kline_intervals=intervals,
            candle_history_limit=cls._coerce_int(
                runtime_section.get("candle_history_limit"),
                default=2048,
            ),
            queue_maxsize=queue_maxsize,
            event_bus_maxsize=event_bus_maxsize,
            stale_after_seconds=cls._coerce_float(
                runtime_section.get("stale_after_seconds"),
                default=30.0,
            ),
            housekeeping_interval_seconds=float(
                cls._coerce_float(
                    runtime_section.get("housekeeping_interval_seconds"),
                    default=60.0,
                )
            ),
            market_data_source=market_data_source,
            sandbox_execution=cls._coerce_bool(
                runtime_section.get("sandbox_execution"),
                exchange_section.get("sandbox"),
                trading_section.get("sandbox_execution"),
                default=False,
            ),
            sandbox_market_data=cls._coerce_bool(
                runtime_section.get("sandbox_market_data"),
                exchange_section.get("market_data_sandbox"),
                trading_section.get("sandbox_market_data"),
                default=False,
            ),
            deployment_profile=runtime_section.get("deployment_profile"),
            runtime_mode=runtime_section.get("runtime_mode"),
            shadow_mode=(
                cls._coerce_bool(runtime_section.get("shadow_mode"), default=True)
                if "shadow_mode" in runtime_section
                else None
            ),
            allow_live_runtime=cls._coerce_bool(
                runtime_section.get("allow_live_runtime"),
                default=False,
            ),
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
            cleaned = values.strip()
            return (cleaned,) if cleaned else ()
        if isinstance(values, Sequence):
            cleaned_values = []
            for value in values:
                if value is None:
                    continue
                text = str(value).strip()
                if text:
                    cleaned_values.append(text)
            return tuple(cleaned_values)
        return ()

    @staticmethod
    def _as_mapping(value: Any) -> Mapping[str, Any]:
        if isinstance(value, Mapping):
            return value
        return {}

    @classmethod
    def _resolve_symbols(
        cls,
        runtime_section: Mapping[str, Any],
        trading_section: Mapping[str, Any],
    ) -> tuple[str, ...]:
        runtime_symbols = cls._as_string_tuple(runtime_section.get("symbols", ()))
        if runtime_symbols:
            return runtime_symbols
        return cls._as_string_tuple(trading_section.get("pairs", ()))

    @classmethod
    def _resolve_intervals(
        cls,
        runtime_section: Mapping[str, Any],
        trading_section: Mapping[str, Any],
    ) -> tuple[str, ...]:
        runtime_intervals = cls._as_string_tuple(
            runtime_section.get("kline_intervals", ())
        )
        if runtime_intervals:
            return runtime_intervals

        timeframes = cls._as_mapping(trading_section.get("timeframes"))
        preferred_order = ("primary", "secondary", "tertiary", "quaternary")
        intervals: list[str] = []
        for key in preferred_order:
            value = timeframes.get(key)
            if value is None:
                continue
            interval = str(value).strip()
            if interval:
                intervals.append(interval)

        if intervals:
            return tuple(intervals)

        configured_intervals = cls._as_string_tuple(
            trading_section.get("timeframes", ())
        )
        if configured_intervals:
            return configured_intervals

        return ("1m",)

    @staticmethod
    def _resolve_runtime_mode(
        value: RuntimeMode | str | None,
        *,
        shadow_mode: bool | None,
    ) -> RuntimeMode:
        if value is None:
            if shadow_mode is True:
                return RuntimeMode.SHADOW
            if shadow_mode is False:
                return RuntimeMode.RUNTIME_ENTRIES
            return RuntimeMode.LEGACY
        return RuntimeMode.parse(value)

    @staticmethod
    def _resolve_deployment_profile(
        value: DeploymentProfile | str | None,
    ) -> DeploymentProfile | None:
        if value is None:
            return None
        return DeploymentProfile.parse(value)

    def validate_startup(self, *, exchange_sandbox: bool) -> None:
        """Raise a readable error when a live runtime-owned mode is unsafe."""

        if exchange_sandbox or not self.runtime_enabled:
            return
        if self.allow_live_runtime:
            return

        startup_target = "runtime-owned mode"
        deployment_profile = ""
        if self.deployment_profile is not None:
            deployment_profile = (
                f", deployment_profile={self.deployment_profile.value!r}"
            )
            if self.deployment_profile.is_live:
                startup_target = (
                    "live runtime canary profile"
                    if self.deployment_profile.is_canary
                    else "live runtime profile"
                )

        raise ValueError(
            f"Refusing to start {startup_target} on a live exchange config: "
            f"runtime_mode={self.runtime_mode.value!r}{deployment_profile}. "
            "Set exchange.sandbox=true for paper/testnet or set "
            "runtime.allow_live_runtime=true to opt in explicitly."
        )

    @staticmethod
    def _resolve_market_data_source(
        runtime_section: Mapping[str, Any],
        exchange_section: Mapping[str, Any],
    ) -> str:
        for candidate in (
            runtime_section.get("market_data_source"),
            exchange_section.get("market_data_source"),
            exchange_section.get("name"),
        ):
            if candidate is None:
                continue
            text = str(candidate).strip()
            if text:
                return text
        return "binance"

    @staticmethod
    def _coerce_bool(*values: Any, default: bool) -> bool:
        for value in values:
            if value is None:
                continue
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().lower()
                if not normalized:
                    continue
                if normalized in {"1", "true", "yes", "y", "on"}:
                    return True
                if normalized in {"0", "false", "no", "n", "off"}:
                    return False
                raise ValueError(f"cannot interpret boolean value: {value!r}")
            return bool(value)
        return default

    @staticmethod
    def _coerce_int(*values: Any, default: int) -> int:
        for value in values:
            if value is None:
                continue
            return int(value)
        return int(default)

    @staticmethod
    def _coerce_float(*values: Any, default: float) -> float:
        for value in values:
            if value is None:
                continue
            return float(value)
        return float(default)

    @property
    def runtime_enabled(self) -> bool:
        return self.runtime_mode.runtime_enabled

    @property
    def runtime_owns_entries(self) -> bool:
        return self.runtime_mode.owns_entries

    @property
    def runtime_owns_exits(self) -> bool:
        return self.runtime_mode.owns_exits

    @property
    def runtime_is_shadow(self) -> bool:
        return self.runtime_mode.shadow_only
