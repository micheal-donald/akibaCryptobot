"""Loss functions used by the Optuna-based hyperopt workflow."""

from __future__ import annotations

from typing import Callable, Dict

from backtesting.engine import BacktestResult


class HyperoptLoss:
    """Base callable loss."""

    name = "base"

    def __call__(self, result: BacktestResult) -> float:
        raise NotImplementedError


class OnlyProfitHyperOptLoss(HyperoptLoss):
    name = "OnlyProfitHyperOptLoss"

    def __call__(self, result: BacktestResult) -> float:
        return -float(result.total_pnl_percentage)


class SharpeHyperOptLoss(HyperoptLoss):
    name = "SharpeHyperOptLoss"

    def __call__(self, result: BacktestResult) -> float:
        return -float(result.sharpe_ratio)


class MaxDrawDownHyperOptLoss(HyperoptLoss):
    name = "MaxDrawDownHyperOptLoss"

    def __call__(self, result: BacktestResult) -> float:
        return float(result.max_drawdown)


class ProfitDrawDownHyperOptLoss(HyperoptLoss):
    name = "ProfitDrawDownHyperOptLoss"

    def __call__(self, result: BacktestResult) -> float:
        drawdown_penalty = float(result.max_drawdown) * 0.25
        return drawdown_penalty - float(result.total_pnl_percentage)


class MultiMetricHyperOptLoss(HyperoptLoss):
    name = "MultiMetricHyperOptLoss"

    def __call__(self, result: BacktestResult) -> float:
        trade_bonus = min(float(result.total_trades), 100.0) / 100.0
        win_rate_score = float(result.win_rate) / 100.0
        profit_factor_score = min(float(result.profit_factor), 5.0) / 5.0
        sharpe_score = min(max(float(result.sharpe_ratio), -5.0), 5.0) / 5.0
        total_score = (
            float(result.total_pnl_percentage) * 0.45
            + win_rate_score * 20.0
            + profit_factor_score * 20.0
            + sharpe_score * 15.0
            + trade_bonus * 5.0
            - float(result.max_drawdown) * 0.6
        )
        return -total_score


LOSS_FUNCTIONS: Dict[str, Callable[[], HyperoptLoss]] = {
    "OnlyProfitHyperOptLoss": OnlyProfitHyperOptLoss,
    "SharpeHyperOptLoss": SharpeHyperOptLoss,
    "MaxDrawDownHyperOptLoss": MaxDrawDownHyperOptLoss,
    "ProfitDrawDownHyperOptLoss": ProfitDrawDownHyperOptLoss,
    "MultiMetricHyperOptLoss": MultiMetricHyperOptLoss,
}


def get_loss_function(name: str) -> HyperoptLoss:
    """Build a loss function by name."""

    try:
        return LOSS_FUNCTIONS[name]()
    except KeyError as exc:
        supported = ", ".join(sorted(LOSS_FUNCTIONS))
        raise ValueError(
            f"Unknown hyperopt loss '{name}'. Supported: {supported}"
        ) from exc
