"""Non-blocking shadow comparison service for market-state parity checks."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from domain.models import SymbolState

from .base import BaseService
from .telemetry_service import TelemetryService


def _state_value(state: Any, name: str) -> Any:
    if isinstance(state, Mapping):
        return state.get(name)
    return getattr(state, name, None)


@dataclass(slots=True)
class ShadowComparator(BaseService):
    """Compare live runtime market state against a legacy market-state provider."""

    current_state_provider: Callable[[], tuple[SymbolState, ...]]
    legacy_state_provider: Callable[[], Mapping[str, Any]]
    telemetry_service: TelemetryService | None = None
    comparison_interval_seconds: float = 5.0
    drift_tolerance_bps: float = 5.0
    _comparison_task: asyncio.Task[None] | None = field(
        default=None, init=False, repr=False
    )
    _comparison_runs: int = field(default=0, init=False, repr=False)
    _drift_count: int = field(default=0, init=False, repr=False)
    _missing_symbol_count: int = field(default=0, init=False, repr=False)
    _last_comparison_at: datetime | None = field(default=None, init=False, repr=False)
    _last_drift_symbols: tuple[str, ...] = field(default=(), init=False, repr=False)

    async def on_start(self) -> None:
        if self._comparison_task is None or self._comparison_task.done():
            self._comparison_task = asyncio.create_task(self._run_comparisons())

    async def on_stop(self) -> None:
        task = self._comparison_task
        self._comparison_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def compare_once(self) -> tuple[str, ...]:
        """Run one non-blocking comparison pass and update internal metrics."""

        current_states = {
            state.symbol: state for state in self.current_state_provider()
        }
        legacy_states = dict(self.legacy_state_provider())
        drift_symbols: list[str] = []

        for symbol in sorted(set(current_states) | set(legacy_states)):
            current_state = current_states.get(symbol)
            legacy_state = legacy_states.get(symbol)
            if current_state is None:
                self._missing_symbol_count += 1
                self._record_missing_symbol(symbol, side="current")
                continue
            if legacy_state is None:
                self._missing_symbol_count += 1
                self._record_missing_symbol(symbol, side="legacy")
                continue
            if self._has_drift(current_state, legacy_state):
                self._drift_count += 1
                drift_symbols.append(symbol)
                self._record_drift(symbol)

        self._comparison_runs += 1
        self._last_comparison_at = datetime.now(timezone.utc)
        self._last_drift_symbols = tuple(drift_symbols)
        return self._last_drift_symbols

    def get_comparison_runs(self) -> int:
        return self._comparison_runs

    def get_drift_count(self) -> int:
        return self._drift_count

    def get_missing_symbol_count(self) -> int:
        return self._missing_symbol_count

    def get_last_comparison_at(self) -> datetime | None:
        return self._last_comparison_at

    def get_last_drift_symbols(self) -> tuple[str, ...]:
        return self._last_drift_symbols

    async def _run_comparisons(self) -> None:
        while True:
            try:
                await self.compare_once()
            except Exception:
                self.logger.exception("Shadow comparison failed")
                if self.telemetry_service is not None:
                    self.telemetry_service.record_metric("shadow_compare_error")
            await asyncio.sleep(max(self.comparison_interval_seconds, 0.01))

    def _record_drift(self, symbol: str) -> None:
        if self.telemetry_service is not None:
            self.telemetry_service.record_shadow_drift(symbol)

    def _record_missing_symbol(self, symbol: str, *, side: str) -> None:
        if self.telemetry_service is not None:
            self.telemetry_service.record_missing_symbol(symbol, side=side)

    def _has_drift(self, current_state: SymbolState, legacy_state: Any) -> bool:
        if self._field_drift(
            _state_value(current_state, "best_bid"),
            _state_value(legacy_state, "best_bid"),
        ):
            return True
        if self._field_drift(
            _state_value(current_state, "best_ask"),
            _state_value(legacy_state, "best_ask"),
        ):
            return True
        if self._field_drift(
            _state_value(current_state, "last_price"),
            _state_value(legacy_state, "last_price"),
        ):
            return True
        if self._field_drift(
            _state_value(current_state, "spread_bps"),
            _state_value(legacy_state, "spread_bps"),
        ):
            return True
        if self._field_drift(
            _state_value(current_state, "last_kline_close"),
            _state_value(legacy_state, "last_kline_close"),
        ):
            return True
        return False

    def _field_drift(self, current_value: Any, legacy_value: Any) -> bool:
        if current_value is None or legacy_value is None:
            return current_value != legacy_value
        if isinstance(current_value, datetime) and isinstance(legacy_value, datetime):
            return current_value != legacy_value
        if isinstance(current_value, (int, float)) and isinstance(
            legacy_value, (int, float)
        ):
            current = float(current_value)
            legacy = float(legacy_value)
            if legacy == 0:
                return current != legacy
            tolerance = abs(legacy) * (self.drift_tolerance_bps / 10000.0)
            return abs(current - legacy) > tolerance
        return current_value != legacy_value
