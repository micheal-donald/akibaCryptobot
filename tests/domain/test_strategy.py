from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from domain.models import Candle
from domain.state import PositionState, PositionStatus
from domain.strategy import RuntimePosition, StrategySnapshot


def test_strategy_snapshot_wraps_runtime_state() -> None:
    candle = Candle(
        open_time=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 3, 22, 9, 1, tzinfo=timezone.utc),
        open=65000.0,
        high=65100.0,
        low=64950.0,
        close=65050.0,
        volume=12.5,
    )
    position = PositionState(
        position_id="pos-1",
        symbol="BTC/USDT",
        side="buy",
        requested_qty=0.5,
        status=PositionStatus.OPEN,
        open_qty=0.5,
    )
    snapshot = StrategySnapshot(
        symbol="BTC/USDT",
        timeframe="1m",
        closed_candles=(candle,),
        best_bid=65040.0,
        best_ask=65060.0,
        last_price=65050.0,
        position=position,
        generated_at=datetime(2026, 3, 22, 9, 1, tzinfo=timezone.utc),
    )

    assert snapshot.symbol == "BTC/USDT"
    assert snapshot.timeframe == "1m"
    assert snapshot.closed_candles == (candle,)
    assert snapshot.position is position
    assert RuntimePosition is PositionState

    with pytest.raises(FrozenInstanceError):
        snapshot.symbol = "ETH/USDT"  # type: ignore[misc]
