"""Strategy snapshot models for runtime-owned signal evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import Candle
from .state import PositionState

RuntimePosition = PositionState


@dataclass(slots=True, frozen=True)
class StrategySnapshot:
    """Typed market snapshot consumed by runtime strategies."""

    symbol: str
    timeframe: str
    closed_candles: tuple[Candle, ...]
    best_bid: float | None
    best_ask: float | None
    last_price: float | None
    position: RuntimePosition | None
    generated_at: datetime


__all__ = ["RuntimePosition", "StrategySnapshot"]
