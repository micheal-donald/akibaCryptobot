"""Replay engine for order-book capture and microstructure research."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from indicators.microstructure_features import (
    MicrostructureFeatureVector,
    feature_vector_from_replay,
)
from runtime.depth_replay import RuntimeDepthReplayHarness
from services.order_book_service import OrderBookService, OrderBookSnapshot
from strategies.microstructure.contracts import (
    MicrostructureDecision,
    MicrostructureShadowStrategyContract,
)


def _mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _safe_mid(snapshot: OrderBookSnapshot) -> float | None:
    return snapshot.mid_price


def _fill_price_for_decision(
    snapshot: OrderBookSnapshot,
    decision: MicrostructureDecision,
) -> float | None:
    if decision.execution_style == "passive":
        if decision.action == "buy" and snapshot.best_bid is not None:
            return snapshot.best_bid.price
        if decision.action == "sell" and snapshot.best_ask is not None:
            return snapshot.best_ask.price
    else:
        if decision.action == "buy" and snapshot.best_ask is not None:
            return snapshot.best_ask.price
        if decision.action == "sell" and snapshot.best_bid is not None:
            return snapshot.best_bid.price
    return None


def _fill_quality_bps(
    *,
    action: str,
    mid_price: float | None,
    fill_price: float | None,
) -> float | None:
    if mid_price is None or mid_price <= 0:
        return None
    if fill_price is None or fill_price <= 0:
        return None
    if action == "buy":
        return ((mid_price - fill_price) / mid_price) * 10_000
    if action == "sell":
        return ((fill_price - mid_price) / mid_price) * 10_000
    return None


def _slippage_bps(
    *,
    action: str,
    decision_price: float | None,
    fill_price: float | None,
) -> float | None:
    if decision_price is None or decision_price <= 0:
        return None
    if fill_price is None or fill_price <= 0:
        return None
    if action == "buy":
        return ((fill_price - decision_price) / decision_price) * 10_000
    if action == "sell":
        return ((decision_price - fill_price) / decision_price) * 10_000
    return None


@dataclass(slots=True, frozen=True)
class ReplayDecisionRecord:
    """Structured decision emitted by the replay engine."""

    event_id: str
    symbol: str
    action: str
    confidence: float
    execution_style: str
    decision_price: float | None
    spread_bps: float | None
    depth_imbalance: float | None
    trade_pressure: float | None
    decision_latency_ms: float
    reason: str


@dataclass(slots=True, frozen=True)
class ReplayFillRecord:
    """Structured fill outcome emitted by the replay engine."""

    event_id: str
    symbol: str
    action: str
    execution_style: str
    decision_price: float | None
    fill_price: float
    mid_price: float | None
    spread_bps: float | None
    decision_latency_ms: float
    submit_latency_ms: float
    fill_quality_bps: float | None
    slippage_bps: float | None
    reason: str


@dataclass(slots=True, frozen=True)
class ReplayCaptureHealth:
    """Capture health summary for a replay source."""

    total_records: int
    depth_records: int
    trade_records: int
    snapshot_records: int
    stale_snapshots: int
    gap_snapshots: int
    sequence_issues: int


@dataclass(slots=True, frozen=True)
class LowLatencyReplayReport:
    """Product report for low-latency replay/shadow evaluation."""

    source: str
    generated_at: str
    capture_health: ReplayCaptureHealth
    feature_rows: int
    decision_count: int
    fill_count: int
    buy_count: int
    sell_count: int
    average_decision_latency_ms: float
    average_submit_latency_ms: float
    average_fill_quality_bps: float
    average_slippage_bps: float
    average_spread_capture_bps: float
    decisions: tuple[ReplayDecisionRecord, ...]
    fills: tuple[ReplayFillRecord, ...]
    feature_vectors: tuple[MicrostructureFeatureVector, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "generated_at": self.generated_at,
            "capture_health": {
                "total_records": self.capture_health.total_records,
                "depth_records": self.capture_health.depth_records,
                "trade_records": self.capture_health.trade_records,
                "snapshot_records": self.capture_health.snapshot_records,
                "stale_snapshots": self.capture_health.stale_snapshots,
                "gap_snapshots": self.capture_health.gap_snapshots,
                "sequence_issues": self.capture_health.sequence_issues,
            },
            "feature_rows": self.feature_rows,
            "decision_count": self.decision_count,
            "fill_count": self.fill_count,
            "buy_count": self.buy_count,
            "sell_count": self.sell_count,
            "average_decision_latency_ms": self.average_decision_latency_ms,
            "average_submit_latency_ms": self.average_submit_latency_ms,
            "average_fill_quality_bps": self.average_fill_quality_bps,
            "average_slippage_bps": self.average_slippage_bps,
            "average_spread_capture_bps": self.average_spread_capture_bps,
            "decisions": [asdict(decision) for decision in self.decisions],
            "fills": [asdict(fill) for fill in self.fills],
            "feature_vectors": [asdict(vector) for vector in self.feature_vectors],
        }


class ReplayEngine:
    """Replay order-book captures into a microstructure shadow strategy."""

    def __init__(
        self,
        strategy: MicrostructureShadowStrategyContract,
        *,
        order_book_service: OrderBookService | None = None,
        decision_latency_ms: float = 5.0,
        submit_latency_ms: float = 2.0,
    ) -> None:
        if decision_latency_ms < 0:
            raise ValueError("decision_latency_ms must be non-negative")
        if submit_latency_ms < 0:
            raise ValueError("submit_latency_ms must be non-negative")
        self.strategy = strategy
        self.order_book_service = order_book_service or OrderBookService(
            name="replay-order-book"
        )
        self.decision_latency_ms = decision_latency_ms
        self.submit_latency_ms = submit_latency_ms

    @classmethod
    def load_capture(
        cls, source: str | Path | Iterable[str]
    ) -> RuntimeDepthReplayHarness:
        return RuntimeDepthReplayHarness.from_jsonl(source)

    def run(
        self, source: str | Path | Iterable[str]
    ) -> LowLatencyReplayReport:
        harness = self.load_capture(source)
        steps = harness.replay_into(self.order_book_service)
        feature_vectors = feature_vector_from_replay(steps)
        vectors_by_event_id = {
            vector.event_id: vector for vector in feature_vectors
        }

        decisions: list[ReplayDecisionRecord] = []
        fills: list[ReplayFillRecord] = []
        sequence_issue_count = 0
        stale_snapshots = 0
        gap_snapshots = 0
        depth_records = 0
        trade_records = 0

        for step in steps:
            record = step.record
            if record.payload_type == "OrderBookSnapshot":
                depth_records += 1
                if step.snapshot is not None:
                    if step.snapshot.is_stale:
                        stale_snapshots += 1
                    if step.snapshot.is_gap_detected or step.sequence_issue == "gap":
                        gap_snapshots += 1
                if step.sequence_issue is not None:
                    sequence_issue_count += 1
            elif record.payload_type == "TradeEvent":
                trade_records += 1

            if step.snapshot is None:
                continue

            vector = vectors_by_event_id.get(record.event_id)
            decision = self.strategy.evaluate(
                step.snapshot,
                vector,
                decision_latency_ms=self.decision_latency_ms,
            )
            if decision is None:
                continue

            decisions.append(
                ReplayDecisionRecord(
                    event_id=record.event_id,
                    symbol=step.snapshot.symbol,
                    action=decision.action,
                    confidence=decision.confidence,
                    execution_style=decision.execution_style,
                    decision_price=decision.decision_price,
                    spread_bps=decision.spread_bps,
                    depth_imbalance=decision.depth_imbalance,
                    trade_pressure=decision.trade_pressure,
                    decision_latency_ms=self.decision_latency_ms,
                    reason=decision.reason,
                )
            )

            fill_price = _fill_price_for_decision(step.snapshot, decision)
            if fill_price is None:
                continue

            mid_price = _safe_mid(step.snapshot)
            fill_quality_bps = _fill_quality_bps(
                action=decision.action,
                mid_price=mid_price,
                fill_price=fill_price,
            )
            slippage_bps = _slippage_bps(
                action=decision.action,
                decision_price=decision.decision_price,
                fill_price=fill_price,
            )
            fills.append(
                ReplayFillRecord(
                    event_id=record.event_id,
                    symbol=step.snapshot.symbol,
                    action=decision.action,
                    execution_style=decision.execution_style,
                    decision_price=decision.decision_price,
                    fill_price=fill_price,
                    mid_price=mid_price,
                    spread_bps=decision.spread_bps,
                    decision_latency_ms=self.decision_latency_ms,
                    submit_latency_ms=self.submit_latency_ms,
                    fill_quality_bps=fill_quality_bps,
                    slippage_bps=slippage_bps,
                    reason=decision.reason,
                )
            )

        return LowLatencyReplayReport(
            source="depth_capture_replay",
            generated_at=datetime.now(timezone.utc).isoformat(),
            capture_health=ReplayCaptureHealth(
                total_records=len(harness.records),
                depth_records=depth_records,
                trade_records=trade_records,
                snapshot_records=sum(
                    1 for record in harness.records if record.payload_type == "OrderBookSnapshot"
                ),
                stale_snapshots=stale_snapshots,
                gap_snapshots=gap_snapshots,
                sequence_issues=sequence_issue_count,
            ),
            feature_rows=len(feature_vectors),
            decision_count=len(decisions),
            fill_count=len(fills),
            buy_count=sum(1 for decision in decisions if decision.action == "buy"),
            sell_count=sum(1 for decision in decisions if decision.action == "sell"),
            average_decision_latency_ms=_mean(
                [decision.decision_latency_ms for decision in decisions]
            ),
            average_submit_latency_ms=_mean(
                [fill.submit_latency_ms for fill in fills]
            ),
            average_fill_quality_bps=_mean(
                [
                    fill.fill_quality_bps
                    for fill in fills
                    if fill.fill_quality_bps is not None
                ]
            ),
            average_slippage_bps=_mean(
                [fill.slippage_bps for fill in fills if fill.slippage_bps is not None]
            ),
            average_spread_capture_bps=_mean(
                [
                    fill.fill_quality_bps
                    for fill in fills
                    if fill.fill_quality_bps is not None
                ]
            ),
            decisions=tuple(decisions),
            fills=tuple(fills),
            feature_vectors=feature_vectors,
        )
