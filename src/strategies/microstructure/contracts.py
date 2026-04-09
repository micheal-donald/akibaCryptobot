"""Microstructure strategy contracts for low-latency shadow research."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from indicators.microstructure_features import MicrostructureFeatureVector
from services.order_book_service import OrderBookSnapshot


@dataclass(slots=True, frozen=True)
class MicrostructureDecision:
    """Strategy decision emitted from a microstructure shadow strategy."""

    action: str
    confidence: float
    execution_style: str
    decision_price: float | None
    spread_bps: float | None
    depth_imbalance: float | None
    trade_pressure: float | None
    reason: str


class MicrostructureShadowStrategyContract(ABC):
    """Contract for microstructure-aware paper-shadow strategy evaluation."""

    @abstractmethod
    def evaluate(
        self,
        snapshot: OrderBookSnapshot,
        features: MicrostructureFeatureVector | None = None,
        *,
        decision_latency_ms: float = 0.0,
    ) -> MicrostructureDecision | None:
        """Return a trade decision from a book snapshot and feature vector."""
