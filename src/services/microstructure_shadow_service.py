"""Live paper-shadow microstructure service.

Connects the order-book/feature/strategy research stack (previously
replay-only) to the live websocket depth and trade streams. The service
never places orders: it evaluates the configured microstructure strategy
on every depth update, records decision and pipeline latency, and emits
``microstructure.decision`` events for warm-path persistence so fill
quality and latency can be analyzed offline.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

from adapters.binance.symbol_mapper import BinanceSymbolMapper
from domain.events import (
    DepthSnapshotPayload,
    EventMeta,
    MicrostructureDecisionPayload,
    RuntimeEvent,
    TradePayload,
)
from indicators.microstructure_features import (
    MicrostructureFeatureVector,
    depth_imbalance,
    spread_bps_from_snapshot,
    spread_from_snapshot,
)
from ports.exchange import DepthStreamGateway
from runtime.config import RuntimeConfig
from strategies.microstructure.contracts import (
    MicrostructureDecision,
    MicrostructureShadowStrategyContract,
)

from .base import BaseService
from .order_book_service import OrderBookService, OrderBookSnapshot
from .telemetry_service import TelemetryService

# Binance partial-book streams only support these level counts.
_SUPPORTED_DEPTH_LEVELS = (5, 10, 20)


@dataclass(slots=True)
class _LatencyAccumulator:
    """Rolling latency statistics without storing samples."""

    count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    last_ms: float | None = None

    def record(self, value_ms: float) -> None:
        self.count += 1
        self.total_ms += value_ms
        self.max_ms = max(self.max_ms, value_ms)
        self.last_ms = value_ms

    def snapshot(self) -> dict[str, float | int | None]:
        return {
            "count": self.count,
            "avg_ms": (self.total_ms / self.count) if self.count else None,
            "max_ms": self.max_ms if self.count else None,
            "last_ms": self.last_ms,
        }


@dataclass(slots=True)
class MicrostructureShadowService(BaseService):
    """Evaluate a microstructure strategy on live depth updates, paper-only."""

    gateway: DepthStreamGateway
    runtime_config: RuntimeConfig
    strategy: MicrostructureShadowStrategyContract
    event_bus: asyncio.Queue[RuntimeEvent] | None = None
    telemetry_service: TelemetryService | None = None
    depth_levels: int = 10
    trade_window: int = 30
    decision_log_size: int = 256
    order_books: OrderBookService = field(init=False)
    _recent_trades: dict[str, deque[tuple[str, float]]] = field(
        default_factory=dict, init=False, repr=False
    )
    _decisions: deque[dict[str, object]] = field(init=False, repr=False)
    _decision_counts: dict[str, int] = field(
        default_factory=dict, init=False, repr=False
    )
    _evaluation_count: int = field(default=0, init=False)
    _subscribe_failures: dict[str, str] = field(
        default_factory=dict, init=False, repr=False
    )
    _decision_latency: _LatencyAccumulator = field(
        default_factory=_LatencyAccumulator, init=False, repr=False
    )
    _pipeline_latency: _LatencyAccumulator = field(
        default_factory=_LatencyAccumulator, init=False, repr=False
    )
    _exchange_to_canonical: dict[str, str] = field(
        default_factory=dict, init=False, repr=False
    )
    _symbol_mapper: BinanceSymbolMapper = field(
        default_factory=BinanceSymbolMapper, init=False, repr=False
    )
    _callbacks_registered: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        BaseService.__post_init__(self)
        if self.depth_levels not in _SUPPORTED_DEPTH_LEVELS:
            raise ValueError(
                f"depth_levels must be one of {_SUPPORTED_DEPTH_LEVELS}, "
                f"got {self.depth_levels}"
            )
        if self.trade_window <= 0:
            raise ValueError("trade_window must be positive")
        if self.decision_log_size <= 0:
            raise ValueError("decision_log_size must be positive")
        self.order_books = OrderBookService(
            name=f"{self.name}-order-book",
            default_depth_window=self.depth_levels,
        )
        self._decisions = deque(maxlen=self.decision_log_size)

    async def on_start(self) -> None:
        self._register_gateway_callbacks()
        await self._subscribe_configured_symbols()

    async def on_stop(self) -> None:
        # The market-data service owns the gateway connection lifecycle.
        return None

    def get_status(self) -> dict[str, object]:
        """Return a read-only operational snapshot for telemetry surfaces."""

        return {
            "strategy": type(self.strategy).__name__,
            "depth_levels": self.depth_levels,
            "trade_window": self.trade_window,
            "evaluations": self._evaluation_count,
            "decision_counts": dict(self._decision_counts),
            "decision_latency": self._decision_latency.snapshot(),
            "pipeline_latency": self._pipeline_latency.snapshot(),
            "last_decision": self._decisions[-1] if self._decisions else None,
            "subscribe_failures": dict(self._subscribe_failures),
            "book_health": {
                symbol: self.order_books.get_state_health(symbol)
                for symbol in self.order_books.symbols()
            },
        }

    def get_recent_decisions(self, limit: int = 50) -> tuple[dict[str, object], ...]:
        if limit <= 0:
            return ()
        return tuple(self._decisions)[-limit:]

    def _register_gateway_callbacks(self) -> None:
        if self._callbacks_registered:
            return
        self.gateway.register_callback("market.depth", self._handle_depth_event)
        self.gateway.register_callback("market.trade", self._handle_trade_event)
        self._callbacks_registered = True

    async def _subscribe_configured_symbols(self) -> None:
        for symbol in self.runtime_config.symbols:
            exchange_symbol = self._to_exchange_symbol(symbol)
            self._exchange_to_canonical[exchange_symbol] = symbol
            # Subscription failures degrade this research surface but must
            # never abort the trading runtime startup.
            try:
                await self.gateway.subscribe_depth(
                    exchange_symbol, depth=self.depth_levels
                )
                await self.gateway.subscribe_trade(exchange_symbol)
            except Exception as exc:
                self._subscribe_failures[symbol] = str(exc)
                self.logger.warning(
                    "microstructure stream subscribe failed for %s: %s", symbol, exc
                )
                if self.telemetry_service is not None:
                    self.telemetry_service.record_metric(
                        "microstructure.subscribe_failure"
                    )

    async def _handle_trade_event(self, event: RuntimeEvent) -> None:
        symbol = self._resolve_canonical_symbol(event.meta.symbol)
        if symbol is None:
            return
        payload = event.payload
        if not isinstance(payload, TradePayload):
            return
        bucket = self._recent_trades.setdefault(symbol, deque(maxlen=self.trade_window))
        bucket.append((payload.side, payload.quantity))

    async def _handle_depth_event(self, event: RuntimeEvent) -> None:
        symbol = self._resolve_canonical_symbol(event.meta.symbol)
        if symbol is None:
            return
        payload = event.payload
        if not isinstance(payload, DepthSnapshotPayload):
            return

        started = time.perf_counter()
        snapshot = self.order_books.replace_snapshot(
            symbol,
            bids=payload.bids,
            asks=payload.asks,
            sequence=payload.sequence,
            exchange_timestamp=event.meta.ts_exchange,
            received_at=event.meta.ts_received,
        )
        features = self._build_features(event.meta.event_id, symbol, snapshot)
        decision_latency_ms = (time.perf_counter() - started) * 1000.0

        self._evaluation_count += 1
        decision = self.strategy.evaluate(
            snapshot, features, decision_latency_ms=decision_latency_ms
        )
        self._decision_latency.record(decision_latency_ms)
        if decision is None:
            return

        decided_at = datetime.now(timezone.utc)
        pipeline_latency_ms = self._age_ms(event.meta.ts_received, decided_at)
        if pipeline_latency_ms is not None:
            self._pipeline_latency.record(pipeline_latency_ms)

        await self._record_decision(
            symbol,
            decision,
            decision_latency_ms=decision_latency_ms,
            pipeline_latency_ms=pipeline_latency_ms,
            book_sequence=snapshot.sequence,
            decided_at=decided_at,
        )

    def _build_features(
        self,
        event_id: str,
        symbol: str,
        snapshot: OrderBookSnapshot,
    ) -> MicrostructureFeatureVector:
        trades = tuple(self._recent_trades.get(symbol, ()))
        buy_volume = sum(qty for side, qty in trades if side == "buy")
        sell_volume = sum(qty for side, qty in trades if side == "sell")
        total_volume = buy_volume + sell_volume
        pressure = (
            (buy_volume - sell_volume) / total_volume if total_volume > 0 else None
        )
        return MicrostructureFeatureVector(
            event_id=event_id,
            symbol=symbol,
            sequence=snapshot.sequence,
            spread=spread_from_snapshot(snapshot),
            spread_bps=spread_bps_from_snapshot(snapshot),
            depth_imbalance=depth_imbalance(snapshot, depth=self.depth_levels),
            trade_pressure=pressure,
            trade_count=len(trades),
            buy_volume=buy_volume,
            sell_volume=sell_volume,
        )

    async def _record_decision(
        self,
        symbol: str,
        decision: MicrostructureDecision,
        *,
        decision_latency_ms: float,
        pipeline_latency_ms: float | None,
        book_sequence: int | None,
        decided_at: datetime,
    ) -> None:
        self._decision_counts[decision.action] = (
            self._decision_counts.get(decision.action, 0) + 1
        )
        record: dict[str, object] = {
            "symbol": symbol,
            "action": decision.action,
            "confidence": decision.confidence,
            "execution_style": decision.execution_style,
            "decision_price": decision.decision_price,
            "spread_bps": decision.spread_bps,
            "depth_imbalance": decision.depth_imbalance,
            "trade_pressure": decision.trade_pressure,
            "reason": decision.reason,
            "decision_latency_ms": round(decision_latency_ms, 3),
            "pipeline_latency_ms": (
                round(pipeline_latency_ms, 3)
                if pipeline_latency_ms is not None
                else None
            ),
            "decided_at": decided_at.isoformat(),
        }
        self._decisions.append(record)
        self.logger.info(
            "microstructure decision %s %s confidence=%.4f reason=%s "
            "decision_latency_ms=%.3f pipeline_latency_ms=%s",
            decision.action,
            symbol,
            decision.confidence,
            decision.reason,
            decision_latency_ms,
            f"{pipeline_latency_ms:.3f}" if pipeline_latency_ms is not None else "n/a",
        )
        if self.telemetry_service is not None:
            self.telemetry_service.record_metric(
                f"microstructure.decision.{decision.action}"
            )
        if self.event_bus is None:
            return
        await self.event_bus.put(
            RuntimeEvent(
                meta=EventMeta(
                    event_id=uuid4().hex,
                    event_type="microstructure.decision",
                    source="runtime.microstructure_shadow",
                    symbol=symbol,
                    ts_exchange=None,
                    ts_received=decided_at,
                ),
                payload=MicrostructureDecisionPayload(
                    symbol=symbol,
                    action=decision.action,
                    confidence=decision.confidence,
                    execution_style=decision.execution_style,
                    decision_price=decision.decision_price,
                    spread_bps=decision.spread_bps,
                    depth_imbalance=decision.depth_imbalance,
                    trade_pressure=decision.trade_pressure,
                    reason=decision.reason,
                    decision_latency_ms=decision_latency_ms,
                    pipeline_latency_ms=pipeline_latency_ms,
                    book_sequence=book_sequence,
                ),
            )
        )

    def _resolve_canonical_symbol(self, symbol: str | None) -> str | None:
        if symbol is None:
            return None
        resolved = self._exchange_to_canonical.get(symbol)
        if resolved is not None:
            return resolved
        try:
            return self._symbol_mapper.to_canonical_symbol(symbol)
        except ValueError:
            return symbol

    def _to_exchange_symbol(self, symbol: str) -> str:
        return self._symbol_mapper.to_exchange_symbol(symbol)

    @staticmethod
    def _age_ms(start: datetime | None, end: datetime) -> float | None:
        if start is None:
            return None
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return cast(float, (end - start).total_seconds() * 1000.0)
