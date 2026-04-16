"""Runtime signal service for candle-close driven strategy evaluation."""

from __future__ import annotations

import asyncio
from collections import Counter, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from domain.events import (
    EventMeta,
    KlinePayload,
    OrderIntentPayload,
    RuntimeEvent,
)
from domain.models import Candle, SymbolState
from domain.strategy import StrategySnapshot
from strategies.contracts import RuntimeStrategy
from strategies.indicator_cache import IndicatorWindowCache

from .base import BaseService
from .telemetry_service import TelemetryService


def _as_aware_utc(value: datetime) -> datetime:
    """Coerce a datetime to tz-aware UTC for safe subtraction.

    See telemetry_service._as_aware_utc for background — kept in-module to
    avoid widening telemetry_service's public surface."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(slots=True, frozen=True)
class SignalSuppressedPayload:
    """Structured explanation for a suppressed runtime signal."""

    symbol: str
    timeframe: str
    reason: str
    candle_close_time: datetime
    strategy_name: str | None
    decision_lag_ms: float | None


@dataclass(slots=True, frozen=True)
class SignalDecisionRecord:
    """Bounded audit record for a runtime strategy decision."""

    symbol: str
    timeframe: str
    candle_close_time: datetime
    outcome: str
    reason: str | None
    decision_lag_ms: float | None
    evaluated_at: datetime


@dataclass(slots=True)
class SignalService(BaseService):
    """Evaluate completed candles and produce runtime signal intents."""

    strategy: RuntimeStrategy
    event_bus: asyncio.Queue[RuntimeEvent]
    event_source: asyncio.Queue[RuntimeEvent] | None = None
    market_state_source: object | None = None
    position_source: object | None = None
    risk_service: object | None = None
    telemetry_service: TelemetryService | None = None
    indicator_cache: IndicatorWindowCache = field(
        default_factory=IndicatorWindowCache, init=False, repr=False
    )
    supported_timeframes: tuple[str, ...] = ()
    max_recent_decisions: int = 256
    is_running: bool = field(default=False, init=False)
    _consumer_task: asyncio.Task[None] | None = field(
        default=None, init=False, repr=False
    )
    _last_processed_close_by_key: dict[tuple[str, str], datetime] = field(
        default_factory=dict, init=False, repr=False
    )
    _last_decision_by_key: dict[tuple[str, str], SignalDecisionRecord] = field(
        default_factory=dict, init=False, repr=False
    )
    _recent_decisions: deque[SignalDecisionRecord] = field(init=False, repr=False)
    _evaluation_counts: Counter[str] = field(
        default_factory=Counter, init=False, repr=False
    )
    _suppression_counts: Counter[str] = field(
        default_factory=Counter, init=False, repr=False
    )
    _exception_counts: Counter[str] = field(
        default_factory=Counter, init=False, repr=False
    )
    _decision_lag_ms: dict[tuple[str, str], float] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        BaseService.__post_init__(self)
        self.indicator_cache = IndicatorWindowCache()
        self._recent_decisions = deque(maxlen=max(int(self.max_recent_decisions), 1))

    async def on_start(self) -> None:
        if self.event_source is None:
            return
        if self._consumer_task is None or self._consumer_task.done():
            self._consumer_task = asyncio.create_task(self._consume_events())

    async def on_stop(self) -> None:
        task = self._consumer_task
        self._consumer_task = None
        if task is None:
            return

        if self.event_source is not None:
            try:
                await asyncio.wait_for(self.event_source.join(), timeout=2.0)
            except TimeoutError:
                pass

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def handle_market_event(self, event: RuntimeEvent) -> None:
        """Process a completed candle event."""

        if event.meta.event_type not in {"market.kline", "market.kline_closed"}:
            return

        payload = event.payload
        if not isinstance(payload, KlinePayload):
            return
        if event.meta.event_type != "market.kline_closed" or not payload.is_closed:
            return

        symbol = event.meta.symbol
        if symbol is None:
            return

        if (
            self.supported_timeframes
            and payload.interval not in self.supported_timeframes
        ):
            await self._suppress(
                payload=payload,
                symbol=symbol,
                reason="unsupported_timeframe",
                evaluated_at=event.meta.ts_received,
            )
            return

        key = (symbol, payload.interval)
        last_close = self._last_processed_close_by_key.get(key)
        if last_close is not None:
            if payload.close_time == last_close:
                await self._suppress(
                    payload=payload,
                    symbol=symbol,
                    reason="duplicate_candle_close",
                    evaluated_at=event.meta.ts_received,
                )
                return
            if payload.close_time < last_close:
                await self._suppress(
                    payload=payload,
                    symbol=symbol,
                    reason="stale_candle_close",
                    evaluated_at=event.meta.ts_received,
                )
                return

        candle = Candle(
            open_time=payload.open_time,
            close_time=payload.close_time,
            open=payload.open,
            high=payload.high,
            low=payload.low,
            close=payload.close,
            volume=payload.volume,
        )
        invalidation_reason = self.indicator_cache.append_candle(
            symbol, payload.interval, candle
        )
        if invalidation_reason is not None and self.telemetry_service is not None:
            self.telemetry_service.record_metric("indicator_cache_invalidated")
            self.telemetry_service.record_metric(
                f"indicator_cache_invalidated.{invalidation_reason}"
            )
            self.telemetry_service.record_metric(
                f"indicator_cache_invalidated.{invalidation_reason}.{payload.interval}"
            )
        snapshot = self._build_snapshot(
            symbol=symbol,
            timeframe=payload.interval,
            generated_at=event.meta.ts_received,
        )

        self._evaluation_counts["evaluated"] += 1
        self._last_processed_close_by_key[key] = payload.close_time

        if self._is_snapshot_stale(symbol):
            await self._suppress(
                payload=payload,
                symbol=symbol,
                reason="stale_symbol_state",
                evaluated_at=event.meta.ts_received,
            )
            return

        try:
            intent = await self.strategy.generate_intent(snapshot)
        except Exception:  # pragma: no cover - defensive runtime isolation
            self._exception_counts["strategy_exception"] += 1
            await self._suppress(
                payload=payload,
                symbol=symbol,
                reason="strategy_exception",
                evaluated_at=event.meta.ts_received,
            )
            return

        if intent is None:
            await self._suppress(
                payload=payload,
                symbol=symbol,
                reason="strategy_returned_none",
                evaluated_at=event.meta.ts_received,
            )
            return

        normalized_intent = self._normalize_intent(intent)
        self._evaluation_counts["generated"] += 1
        self._record_decision(
            symbol=symbol,
            timeframe=payload.interval,
            candle_close_time=payload.close_time,
            outcome="generated",
            reason=None,
            evaluated_at=event.meta.ts_received,
        )
        await self._emit_intent(normalized_intent, snapshot, event.meta.ts_received)

    async def _consume_events(self) -> None:
        assert self.event_source is not None
        while True:
            event = await self.event_source.get()
            try:
                await self.handle_market_event(event)
            except Exception as exc:  # pragma: no cover - defensive isolation
                self.logger.warning("signal event handling failed: %s", exc)
            finally:
                self.event_source.task_done()

    def _build_snapshot(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
    ) -> StrategySnapshot:
        state = self._resolve_symbol_state(symbol)
        position = self._resolve_position(symbol)
        candles = self.indicator_cache.get_window(symbol, timeframe)
        return StrategySnapshot(
            symbol=symbol,
            timeframe=timeframe,
            closed_candles=candles,
            best_bid=None if state is None else state.best_bid,
            best_ask=None if state is None else state.best_ask,
            last_price=None if state is None else state.last_price,
            position=position,
            generated_at=generated_at,
        )

    def _resolve_symbol_state(self, symbol: str) -> SymbolState | None:
        source = self.market_state_source
        if source is None:
            return None
        if hasattr(source, "get_symbol_state"):
            return source.get_symbol_state(symbol)  # type: ignore[attr-defined]
        if hasattr(source, "get_all_symbol_states"):
            for state in source.get_all_symbol_states():  # type: ignore[attr-defined]
                if getattr(state, "symbol", None) == symbol:
                    return state
        return None

    def _resolve_position(self, symbol: str) -> Any | None:
        source = self.position_source
        if source is None:
            return None
        if hasattr(source, "get_position_for_symbol"):
            return source.get_position_for_symbol(symbol)  # type: ignore[attr-defined]
        if hasattr(source, "get_all_position_states"):
            for state in source.get_all_position_states():  # type: ignore[attr-defined]
                if getattr(state, "symbol", None) == symbol:
                    return state
        return None

    async def _emit_intent(
        self,
        intent: OrderIntentPayload,
        snapshot: StrategySnapshot,
        evaluated_at: datetime,
    ) -> None:
        if self.risk_service is not None and hasattr(
            self.risk_service, "submit_order_intent"
        ):
            await self.risk_service.submit_order_intent(intent)
            return

        await self.event_bus.put(
            RuntimeEvent(
                meta=EventMeta(
                    event_id=uuid4().hex,
                    event_type="signal.order_intent",
                    source="signal.service",
                    symbol=intent.symbol,
                    ts_exchange=snapshot.generated_at,
                    ts_received=evaluated_at,
                ),
                payload=intent,
            )
        )

    async def _emit_suppressed_event(
        self,
        *,
        symbol: str,
        timeframe: str,
        reason: str,
        candle_close_time: datetime,
        evaluated_at: datetime,
        strategy_name: str | None,
    ) -> None:
        decision_lag_ms = self._compute_decision_lag_ms(candle_close_time, evaluated_at)
        await self.event_bus.put(
            RuntimeEvent(
                meta=EventMeta(
                    event_id=uuid4().hex,
                    event_type=cast(Any, "signal.suppressed"),
                    source="signal.service",
                    symbol=symbol,
                    ts_exchange=candle_close_time,
                    ts_received=evaluated_at,
                ),
                payload=SignalSuppressedPayload(
                    symbol=symbol,
                    timeframe=timeframe,
                    reason=reason,
                    candle_close_time=candle_close_time,
                    strategy_name=strategy_name,
                    decision_lag_ms=decision_lag_ms,
                ),
            )
        )
        self._decision_lag_ms[(symbol, timeframe)] = decision_lag_ms

    async def _suppress(
        self,
        *,
        payload: KlinePayload,
        symbol: str,
        reason: str,
        evaluated_at: datetime,
    ) -> None:
        self._suppression_counts[reason] += 1
        decision_lag_ms = self._compute_decision_lag_ms(
            payload.close_time, evaluated_at
        )
        self._record_decision(
            symbol=symbol,
            timeframe=payload.interval,
            candle_close_time=payload.close_time,
            outcome="suppressed",
            reason=reason,
            evaluated_at=evaluated_at,
            decision_lag_ms=decision_lag_ms,
        )
        self._decision_lag_ms[(symbol, payload.interval)] = decision_lag_ms
        await self._emit_suppressed_event(
            symbol=symbol,
            timeframe=payload.interval,
            reason=reason,
            candle_close_time=payload.close_time,
            evaluated_at=evaluated_at,
            strategy_name=self._strategy_name,
        )

    def _record_decision(
        self,
        *,
        symbol: str,
        timeframe: str,
        candle_close_time: datetime,
        outcome: str,
        reason: str | None,
        evaluated_at: datetime,
        decision_lag_ms: float | None = None,
    ) -> None:
        record = SignalDecisionRecord(
            symbol=symbol,
            timeframe=timeframe,
            candle_close_time=candle_close_time,
            outcome=outcome,
            reason=reason,
            decision_lag_ms=decision_lag_ms,
            evaluated_at=evaluated_at,
        )
        self._last_decision_by_key[(symbol, timeframe)] = record
        self._recent_decisions.append(record)

    def _is_snapshot_stale(self, symbol: str) -> bool:
        state = self._resolve_symbol_state(symbol)
        return bool(state is not None and getattr(state, "is_stale", False))

    @staticmethod
    def _compute_decision_lag_ms(
        candle_close_time: datetime, evaluated_at: datetime
    ) -> float:
        # Either side can leak in as naive (e.g. ws_gateway forwards a naive
        # timestamp unchanged). Mixing naive/aware raises TypeError, which
        # historically cascaded up into the runtime-router warning log.
        candle_close_time = _as_aware_utc(candle_close_time)
        evaluated_at = _as_aware_utc(evaluated_at)
        delta = evaluated_at - candle_close_time
        return max(delta.total_seconds() * 1000.0, 0.0)

    def _normalize_intent(self, intent: OrderIntentPayload) -> OrderIntentPayload:
        if intent.strategy is not None:
            return intent
        return replace(intent, strategy=self._strategy_name)

    @property
    def _strategy_name(self) -> str | None:
        return getattr(self.strategy, "name", type(self.strategy).__name__)

    def get_evaluation_counts(self) -> dict[str, int]:
        return dict(self._evaluation_counts)

    def get_suppression_counts(self) -> dict[str, int]:
        return dict(self._suppression_counts)

    def get_exception_counts(self) -> dict[str, int]:
        return dict(self._exception_counts)

    def get_last_decision(
        self, symbol: str, timeframe: str
    ) -> SignalDecisionRecord | None:
        return self._last_decision_by_key.get((symbol, timeframe))

    def get_recent_decisions(self) -> tuple[SignalDecisionRecord, ...]:
        return tuple(self._recent_decisions)


__all__ = [
    "SignalDecisionRecord",
    "SignalService",
    "SignalSuppressedPayload",
]
