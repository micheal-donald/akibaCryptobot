"""Paper-shadow microstructure strategy contract."""

from __future__ import annotations

from dataclasses import dataclass

from indicators.microstructure_features import MicrostructureFeatureVector
from services.order_book_service import OrderBookSnapshot

from .contracts import MicrostructureDecision, MicrostructureShadowStrategyContract


@dataclass(slots=True)
class PaperShadowMicrostructureStrategy(MicrostructureShadowStrategyContract):
    """Simple spread/imbalance strategy for paper-shadow validation."""

    min_abs_imbalance: float = 0.2
    max_spread_bps: float = 30.0
    min_trade_pressure: float = 0.05
    min_confidence: float = 0.55
    latency_budget_ms: float = 25.0
    prefer_passive_execution: bool = True

    def evaluate(
        self,
        snapshot: OrderBookSnapshot,
        features: MicrostructureFeatureVector | None = None,
        *,
        decision_latency_ms: float = 0.0,
    ) -> MicrostructureDecision | None:
        spread_bps = features.spread_bps if features is not None else None
        if spread_bps is None:
            spread_bps = _spread_bps(snapshot)

        imbalance = features.depth_imbalance if features is not None else None
        if imbalance is None:
            imbalance = _depth_imbalance(snapshot)

        pressure = features.trade_pressure if features is not None else None
        if pressure is None:
            pressure = 0.0

        if decision_latency_ms > self.latency_budget_ms:
            return None
        if spread_bps is None or spread_bps > self.max_spread_bps:
            return None
        if imbalance is None:
            return None

        bullish_signal = (
            imbalance >= self.min_abs_imbalance and pressure >= 0.0
        ) or (
            pressure >= self.min_trade_pressure and imbalance >= 0.0
        )
        if bullish_signal:
            confidence = _confidence(
                imbalance,
                pressure,
                spread_bps,
                self.max_spread_bps,
                decision_latency_ms,
                self.latency_budget_ms,
            )
            if confidence < self.min_confidence:
                return None
            return MicrostructureDecision(
                action="buy",
                confidence=confidence,
                execution_style="passive" if self.prefer_passive_execution else "aggressive",
                decision_price=snapshot.best_bid.price if snapshot.best_bid else None,
                spread_bps=spread_bps,
                depth_imbalance=imbalance,
                trade_pressure=pressure,
                reason="bullish_imbalance",
            )

        bearish_signal = (
            imbalance <= -self.min_abs_imbalance and pressure <= 0.0
        ) or (
            pressure <= -self.min_trade_pressure and imbalance <= 0.0
        )
        if bearish_signal:
            confidence = _confidence(
                abs(imbalance),
                abs(pressure),
                spread_bps,
                self.max_spread_bps,
                decision_latency_ms,
                self.latency_budget_ms,
            )
            if confidence < self.min_confidence:
                return None
            return MicrostructureDecision(
                action="sell",
                confidence=confidence,
                execution_style="passive" if self.prefer_passive_execution else "aggressive",
                decision_price=snapshot.best_ask.price if snapshot.best_ask else None,
                spread_bps=spread_bps,
                depth_imbalance=imbalance,
                trade_pressure=pressure,
                reason="bearish_imbalance",
            )

        return None


def _spread_bps(snapshot: OrderBookSnapshot) -> float | None:
    if snapshot.spread is None or snapshot.mid_price is None or snapshot.mid_price <= 0:
        return None
    return (snapshot.spread / snapshot.mid_price) * 10_000


def _depth_imbalance(snapshot: OrderBookSnapshot) -> float | None:
    bid_volume = sum(level.quantity for level in snapshot.bids if level.quantity > 0)
    ask_volume = sum(level.quantity for level in snapshot.asks if level.quantity > 0)
    total_volume = bid_volume + ask_volume
    if total_volume <= 0:
        return None
    return (bid_volume - ask_volume) / total_volume


def _confidence(
    imbalance: float,
    pressure: float,
    spread_bps: float,
    max_spread_bps: float,
    decision_latency_ms: float,
    latency_budget_ms: float,
) -> float:
    spread_component = max(0.0, 1.0 - (spread_bps / max_spread_bps))
    latency_component = max(0.0, 1.0 - (decision_latency_ms / latency_budget_ms))
    core = min(1.0, abs(imbalance) + abs(pressure))
    return round(min(1.0, 0.4 * core + 0.35 * spread_component + 0.25 * latency_component), 4)
