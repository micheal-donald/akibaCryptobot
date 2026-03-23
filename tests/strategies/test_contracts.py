from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from domain.events import OrderIntentPayload
from domain.models import Candle
from domain.strategy import StrategySnapshot
from strategies.contracts import RuntimeStrategy


class FakeRuntimeStrategy:
    async def generate_intent(
        self, snapshot: StrategySnapshot
    ) -> OrderIntentPayload | None:
        if snapshot.last_price is None:
            return None
        return OrderIntentPayload(
            intent_id=f"{snapshot.symbol}-{snapshot.timeframe}",
            symbol=snapshot.symbol,
            side="buy",
            order_type="market",
            quantity=0.01,
            reason="runtime_contract",
        )


def test_runtime_strategy_protocol_accepts_async_strategy() -> None:
    strategy = FakeRuntimeStrategy()
    candle = Candle(
        open_time=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 3, 22, 9, 1, tzinfo=timezone.utc),
        open=65000.0,
        high=65100.0,
        low=64950.0,
        close=65050.0,
        volume=12.5,
    )
    snapshot = StrategySnapshot(
        symbol="BTC/USDT",
        timeframe="1m",
        closed_candles=(candle,),
        best_bid=65040.0,
        best_ask=65060.0,
        last_price=65050.0,
        position=None,
        generated_at=datetime(2026, 3, 22, 9, 1, tzinfo=timezone.utc),
    )

    async def run() -> OrderIntentPayload | None:
        assert isinstance(strategy, RuntimeStrategy)
        return await strategy.generate_intent(snapshot)

    intent = asyncio.run(run())

    assert intent is not None
    assert intent.intent_id == "BTC/USDT-1m"
    assert intent.strategy is None
    assert intent.reason == "runtime_contract"
