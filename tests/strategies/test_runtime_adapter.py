from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pandas as pd
import pytest

from domain.models import Candle
from domain.state import PositionState, PositionStatus
from domain.strategy import StrategySnapshot
from strategies.base_strategy import BaseStrategy, SignalType, TradingSignal
from strategies.runtime_adapter import LegacyStrategyRuntimeAdapter


class FakeLegacyStrategy(BaseStrategy):
    def __init__(self, signal: TradingSignal | None, exit_signal: TradingSignal | None):
        super().__init__({})
        self._signal = signal
        self._exit_signal = exit_signal
        self.name = "fake_legacy"

    async def generate_signal(
        self, symbol: str, ohlcv_data: pd.DataFrame, current_price: float
    ) -> TradingSignal | None:
        assert symbol == "BTC/USDT"
        assert not ohlcv_data.empty
        assert current_price > 0
        return self._signal

    def calculate_indicators(self, ohlcv_data: pd.DataFrame) -> pd.DataFrame:
        return ohlcv_data

    async def should_exit_position(
        self,
        symbol: str,
        current_position: dict,
        current_price: float,
        ohlcv_data: pd.DataFrame,
    ) -> TradingSignal | None:
        assert symbol == "BTC/USDT"
        assert current_position["side"] == "buy"
        assert current_price > 0
        assert not ohlcv_data.empty
        return self._exit_signal


def make_snapshot(position: PositionState | None = None) -> StrategySnapshot:
    candle = Candle(
        open_time=datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 3, 24, 10, 1, tzinfo=timezone.utc),
        open=65000.0,
        high=65100.0,
        low=64950.0,
        close=65050.0,
        volume=12.5,
    )
    return StrategySnapshot(
        symbol="BTC/USDT",
        timeframe="1m",
        closed_candles=(candle,),
        best_bid=65040.0,
        best_ask=65060.0,
        last_price=65050.0,
        position=position,
        generated_at=datetime(2026, 3, 24, 10, 1, 5, tzinfo=timezone.utc),
    )


def test_runtime_adapter_maps_entry_signal_to_market_intent() -> None:
    signal = TradingSignal(
        signal_type=SignalType.BUY,
        strength=0.8,
        price=65050.0,
        timestamp=datetime(2026, 3, 24, 10, 1, tzinfo=timezone.utc),
        stop_loss=64000.0,
        take_profit=67000.0,
    )
    adapter = LegacyStrategyRuntimeAdapter(
        strategy=FakeLegacyStrategy(signal=signal, exit_signal=None),
        entry_quantity=0.02,
    )

    async def run():
        return await adapter.generate_intent(make_snapshot())

    intent = asyncio.run(run())

    assert intent is not None
    assert intent.side == "buy"
    assert intent.reduce_only is False
    assert intent.quantity == 0.02
    assert intent.stop_loss == 64000.0
    assert intent.take_profit == 67000.0
    assert intent.strategy == "fake_legacy"


@pytest.mark.parametrize("signal_type", [SignalType.CLOSE_LONG, SignalType.SELL])
def test_runtime_adapter_maps_exit_signal_to_reduce_only_intent(signal_type) -> None:
    exit_signal = TradingSignal(
        signal_type=signal_type,
        strength=1.0,
        price=65050.0,
        timestamp=datetime(2026, 3, 24, 10, 1, tzinfo=timezone.utc),
        metadata={"exit_reason": "take_profit"},
    )
    position = PositionState(
        position_id="pos-1",
        symbol="BTC/USDT",
        side="buy",
        requested_qty=0.5,
        status=PositionStatus.OPEN,
        open_qty=0.5,
        avg_entry_price=64500.0,
    )
    adapter = LegacyStrategyRuntimeAdapter(
        strategy=FakeLegacyStrategy(signal=None, exit_signal=exit_signal),
        entry_quantity=0.02,
    )

    async def run():
        return await adapter.generate_intent(make_snapshot(position=position))

    intent = asyncio.run(run())

    assert intent is not None
    assert intent.side == "sell"
    assert intent.reduce_only is True
    assert intent.quantity == 0.5
    assert intent.reason == "take_profit"


def test_runtime_adapter_intent_id_is_deterministic_for_same_snapshot() -> None:
    signal = TradingSignal(
        signal_type=SignalType.SELL,
        strength=0.7,
        price=65050.0,
        timestamp=datetime(2026, 3, 24, 10, 1, tzinfo=timezone.utc),
    )
    adapter = LegacyStrategyRuntimeAdapter(
        strategy=FakeLegacyStrategy(signal=signal, exit_signal=None),
        entry_quantity=0.02,
    )
    snapshot = make_snapshot()

    async def run():
        first = await adapter.generate_intent(snapshot)
        second = await adapter.generate_intent(snapshot)
        return first, second

    first, second = asyncio.run(run())

    assert first is not None
    assert second is not None
    assert first.intent_id == second.intent_id
    assert first.side == "sell"
