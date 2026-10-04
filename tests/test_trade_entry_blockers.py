"""Regression coverage for unavailable ML votes and runtime stake sizing."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from bot import AkibaCryptoBot
from risk_management import RiskManager
from strategies.base_strategy import SignalType, TradingSignal
from strategies.ensemble_strategy import EnsembleStrategy


def buy_signal(strength=0.6):
    return TradingSignal(
        signal_type=SignalType.BUY,
        strength=strength,
        price=50000,
        timestamp=datetime.now(),
        stop_loss=49000,
        take_profit=52000,
        metadata={},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("model_state", ["missing", "expired", "disabled"])
async def test_unavailable_ml_preserves_momentum_strength(model_state):
    strategy = EnsembleStrategy(
        {
            "freqai": {"enabled": True},
            "strategy_config": {"min_signal_strength": 0.35},
        }
    )
    if model_state == "expired":
        strategy.freqai.registry.register(
            "BTC/USDT",
            model=object(),
            feature_columns=["a"],
            trained_at=datetime.now(),
            is_expired=True,
        )
    elif model_state == "disabled":
        strategy.freqai.freqai_enabled = False
    strategy.momentum.generate_signal = AsyncMock(return_value=buy_signal())
    strategy.freqai.generate_signal = AsyncMock(return_value=None)

    signal = await strategy.generate_signal("BTC/USDT", pd.DataFrame(), 50000)

    assert signal is not None
    assert signal.signal_type is SignalType.BUY
    assert signal.strength == pytest.approx(0.6)
    assert signal.metadata["available_strategies"] == ["momentum"]
    strategy.freqai.generate_signal.assert_not_awaited()


@pytest.mark.asyncio
async def test_available_ml_hold_keeps_original_consensus_weights():
    strategy = EnsembleStrategy({"freqai": {"enabled": True}})
    strategy.freqai.registry.register(
        "BTC/USDT",
        model=object(),
        feature_columns=["a"],
        trained_at=datetime.now(),
        is_expired=False,
    )
    strategy.momentum.generate_signal = AsyncMock(return_value=buy_signal())
    strategy.freqai.generate_signal = AsyncMock(return_value=None)
    assert await strategy.generate_signal("BTC/USDT", pd.DataFrame(), 50000) is None
    strategy.freqai.generate_signal.assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_respects_explicit_two_component_agreement():
    strategy = EnsembleStrategy({"ensemble": {"min_strategies_agree": 2}})
    strategy.momentum.generate_signal = AsyncMock(return_value=buy_signal())
    assert await strategy.generate_signal("BTC/USDT", pd.DataFrame(), 50000) is None


@pytest.fixture
def runtime_bot():
    bot = AkibaCryptoBot.__new__(AkibaCryptoBot)
    bot.config = {
        "trading": {"stake_amount": 0.05, "stake_currency": "USDT"},
        "strategy_config": {"min_signal_strength": 0.35},
    }
    bot._cached_balance = {"USDT": {"free": 1000, "used": 200}}
    bot.risk_manager = RiskManager({"max_position_size": 0.05})
    bot.logger = MagicMock()
    return bot


def snapshot(price=50000, position=None):
    return SimpleNamespace(
        symbol="BTC/USDT", last_price=price, closed_candles=(), position=position
    )


def test_runtime_rejects_below_exchange_minimum_without_raising_risk(runtime_bot):
    runtime_bot._cached_balance = {"USDT": {"free": 84}}
    runtime_bot.exchange = SimpleNamespace(
        validate_and_adjust_order=lambda symbol, amount, price: (
            amount * price >= 5,
            amount,
        )
    )
    assert runtime_bot._resolve_runtime_entry_quantity(snapshot(), buy_signal()) == 0


def test_runtime_stake_is_fraction_of_free_balance(runtime_bot):
    quantity = runtime_bot._resolve_runtime_entry_quantity(snapshot(), buy_signal())
    assert quantity == pytest.approx(0.001)
    assert quantity * 50000 == pytest.approx(50)


def test_runtime_stake_respects_risk_cap(runtime_bot):
    runtime_bot.config["trading"]["stake_amount"] = 0.5
    assert runtime_bot._resolve_runtime_entry_quantity(
        snapshot(), buy_signal()
    ) == pytest.approx(0.001)


@pytest.mark.parametrize("free_balance", [0, None])
def test_runtime_does_not_size_without_available_balance(runtime_bot, free_balance):
    runtime_bot._cached_balance = (
        {"USDT": {"free": free_balance}} if free_balance is not None else None
    )
    assert runtime_bot._resolve_runtime_entry_quantity(snapshot(), buy_signal()) == 0


def test_runtime_rejects_weak_entry_signal(runtime_bot):
    assert runtime_bot._resolve_runtime_entry_quantity(snapshot(), buy_signal(0.2)) == 0


def test_runtime_exit_preserves_open_quantity_without_cash(runtime_bot):
    runtime_bot._cached_balance = None
    quantity = runtime_bot._resolve_runtime_entry_quantity(
        snapshot(position=SimpleNamespace(open_qty=0.002)), buy_signal(0.2)
    )
    assert quantity == pytest.approx(0.002)
