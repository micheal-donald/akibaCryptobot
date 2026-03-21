import os
import sys
from datetime import datetime

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from backtesting.engine import BacktestResult
from optimization.hyperopt import StrategyHyperoptimizer
from optimization.loss_functions import (
    MaxDrawDownHyperOptLoss,
    MultiMetricHyperOptLoss,
    ProfitDrawDownHyperOptLoss,
    SharpeHyperOptLoss,
)
from optimization.parameters import (
    BooleanParameter,
    CategoricalParameter,
    DecimalParameter,
    IntParameter,
    resolve_spaces,
)
from strategies.ensemble_strategy import EnsembleStrategy
from strategies.momentum_strategy import MomentumStrategy


class FakeTrial:
    def __init__(self):
        self.calls = []

    def suggest_int(self, name, low, high, step=1):
        self.calls.append(("int", name, low, high, step))
        return low + step

    def suggest_float(self, name, low, high, step=0.001):
        self.calls.append(("float", name, low, high, step))
        return round(low + step, 8)

    def suggest_categorical(self, name, choices):
        self.calls.append(("categorical", name, tuple(choices)))
        return choices[0]


@pytest.fixture
def base_config():
    return {
        "trading": {
            "pairs": ["BTC/USDT"],
            "timeframes": {"primary": "1h"},
            "strategy": "momentum",
            "max_open_trades": 3,
        },
        "backtesting": {
            "initial_balance": 10000,
            "fee": 0.001,
            "timerange": "20240101-20240601",
        },
        "risk_management": {
            "stop_loss_percent": 0.02,
            "take_profit_percent": 0.04,
            "trailing_stop": True,
            "trailing_stop_positive": 0.01,
        },
        "strategy_config": {
            "min_signal_strength": 0.35,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "stoch_oversold": 20,
            "stoch_overbought": 80,
            "adx_threshold": 25,
            "min_volume_ratio": 1.2,
        },
    }


@pytest.fixture
def sample_result():
    return BacktestResult(
        total_trades=15,
        winning_trades=9,
        losing_trades=6,
        total_pnl=250.0,
        total_pnl_percentage=2.5,
        win_rate=60.0,
        profit_factor=1.8,
        max_drawdown=8.0,
        sharpe_ratio=1.4,
        avg_win=45.0,
        avg_loss=20.0,
        max_consecutive_wins=4,
        max_consecutive_losses=2,
        trades=[],
        equity_curve=[10000, 10100, 10250],
        timestamps=[datetime(2024, 1, 1), datetime(2024, 1, 2), datetime(2024, 1, 3)],
    )


def test_parameter_primitives_delegate_to_trial():
    trial = FakeTrial()

    assert (
        IntParameter(name="lookback", default=10, low=5, high=20, step=5).suggest(trial)
        == 10
    )
    assert DecimalParameter(
        name="threshold", default=0.2, low=0.1, high=0.5, step=0.05
    ).suggest(trial) == pytest.approx(0.15)
    assert (
        CategoricalParameter(name="mode", default="a", choices=["a", "b"]).suggest(
            trial
        )
        == "a"
    )
    assert BooleanParameter(name="flag", default=True).suggest(trial) is True
    assert len(trial.calls) == 4


def test_resolve_spaces_expands_default_and_all():
    available = ["buy", "sell", "roi", "stoploss", "trailing", "trades"]
    assert resolve_spaces(["default"], available) == ["buy", "sell", "roi", "stoploss"]
    assert resolve_spaces(["all"], available) == available


def test_momentum_strategy_exposes_hyperopt_parameters():
    parameters = MomentumStrategy.hyperopt_parameters()
    names = {parameter.name for parameter in parameters}

    assert "rsi_oversold" in names
    assert "rsi_overbought" in names
    assert "min_signal_strength" in names
    assert {parameter.space for parameter in parameters} >= {"buy", "sell"}


def test_ensemble_config_application_normalizes_weights(base_config):
    updated = EnsembleStrategy.apply_hyperopt_parameters_to_config(
        base_config,
        {
            "momentum_weight": 0.8,
            "freqai_weight": 0.8,
            "min_consensus": 0.45,
        },
    )

    ensemble = updated["ensemble"]
    assert ensemble["momentum_weight"] == pytest.approx(0.5)
    assert ensemble["freqai_weight"] == pytest.approx(0.5)
    assert ensemble["min_consensus"] == pytest.approx(0.45)


def test_loss_functions_return_expected_direction(sample_result):
    assert ProfitDrawDownHyperOptLoss()(sample_result) < 0
    assert SharpeHyperOptLoss()(sample_result) < 0
    assert MultiMetricHyperOptLoss()(sample_result) < 0
    assert MaxDrawDownHyperOptLoss()(sample_result) == pytest.approx(8.0)


def test_hyperoptimizer_collects_spaces_and_applies_params(base_config):
    optimizer = StrategyHyperoptimizer(
        config=base_config,
        strategy_name="momentum",
        spaces=["buy", "roi", "stoploss", "trailing", "trades"],
        min_trades=3,
    )

    selected = {parameter.name for parameter in optimizer.selected_parameters}
    assert "rsi_oversold" in selected
    assert "stop_loss_percent" in selected
    assert "take_profit_percent" in selected
    assert "max_open_trades" in selected

    applied = optimizer._apply_params(
        {
            "rsi_oversold": 25,
            "stop_loss_percent": 0.03,
            "take_profit_percent": 0.06,
            "max_open_trades": 5,
        }
    )
    assert applied["strategy_config"]["rsi_oversold"] == 25
    assert applied["risk_management"]["stop_loss_percent"] == pytest.approx(0.03)
    assert applied["risk_management"]["take_profit_percent"] == pytest.approx(0.06)
    assert applied["trading"]["max_open_trades"] == 5
