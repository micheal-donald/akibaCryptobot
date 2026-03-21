"""Optuna-backed hyperparameter optimization for AkibaCryptoBot."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence, Type

from backtesting.engine import BacktestEngine, BacktestResult
from optimization.loss_functions import HyperoptLoss, get_loss_function
from optimization.parameters import (
    BooleanParameter,
    DecimalParameter,
    HyperParameter,
    IntParameter,
    resolve_spaces,
    select_parameters,
)
from strategies.base_strategy import BaseStrategy
from strategies.ensemble_strategy import EnsembleStrategy
from strategies.freqai.ml_freqai_strategy import MLFreqAIStrategy
from strategies.momentum_strategy import MomentumStrategy


STRATEGY_REGISTRY: Dict[str, Type[BaseStrategy]] = {
    "ensemble": EnsembleStrategy,
    "freqai_ml_freqai": MLFreqAIStrategy,
    "momentum": MomentumStrategy,
}


@dataclass
class HyperoptTrialResult:
    number: int
    loss: float
    params: Dict[str, Any]
    total_trades: int
    total_pnl: float
    total_pnl_percentage: float
    win_rate: float
    profit_factor: float
    max_drawdown: float
    sharpe_ratio: float

    @classmethod
    def from_backtest(
        cls,
        number: int,
        loss: float,
        params: Dict[str, Any],
        result: BacktestResult,
    ) -> "HyperoptTrialResult":
        return cls(
            number=number,
            loss=loss,
            params=params,
            total_trades=result.total_trades,
            total_pnl=result.total_pnl,
            total_pnl_percentage=result.total_pnl_percentage,
            win_rate=result.win_rate,
            profit_factor=result.profit_factor,
            max_drawdown=result.max_drawdown,
            sharpe_ratio=result.sharpe_ratio,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HyperoptSummary:
    strategy: str
    loss_function: str
    spaces: List[str]
    best: HyperoptTrialResult
    top_results: List[HyperoptTrialResult]
    merged_config: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "loss_function": self.loss_function,
            "spaces": self.spaces,
            "best": self.best.to_dict(),
            "top_results": [result.to_dict() for result in self.top_results],
            "merged_config": self.merged_config,
        }

    def export_json(self, path: str) -> None:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2)


class StrategyHyperoptimizer:
    """Run Optuna trials against the project's existing backtesting engine."""

    def __init__(
        self,
        config: Dict[str, Any],
        strategy_name: str,
        loss_name: str = "ProfitDrawDownHyperOptLoss",
        spaces: Sequence[str] | None = None,
        min_trades: int = 1,
        random_state: int = 42,
    ) -> None:
        if strategy_name not in STRATEGY_REGISTRY:
            supported = ", ".join(available_strategies())
            raise ValueError(
                f"Unknown strategy '{strategy_name}'. Supported strategies: {supported}"
            )

        self.base_config = copy.deepcopy(config)
        self.strategy_name = strategy_name
        self.strategy_class = STRATEGY_REGISTRY[strategy_name]
        self.loss_function: HyperoptLoss = get_loss_function(loss_name)
        self.loss_name = loss_name
        self.min_trades = max(min_trades, 1)
        self.random_state = random_state

        self.parameters = self._collect_parameters()
        available_spaces = [parameter.space for parameter in self.parameters]
        self.spaces = resolve_spaces(spaces, available_spaces)
        self.selected_parameters = select_parameters(self.parameters, self.spaces)

        if not self.selected_parameters:
            available = ", ".join(sorted(set(available_spaces)))
            raise ValueError(
                f"No hyperopt parameters available for spaces {spaces}. Available spaces: {available}"
            )

    def optimize(
        self,
        data: Dict[str, Any],
        epochs: int = 100,
        jobs: int = 1,
        early_stop: int = 0,
        top_n: int = 5,
    ) -> HyperoptSummary:
        optuna = _load_optuna()
        sampler = optuna.samplers.TPESampler(seed=self.random_state)
        study = optuna.create_study(direction="minimize", sampler=sampler)

        stale_trials = 0

        def callback(study_obj: Any, trial: Any) -> None:
            nonlocal stale_trials
            if study_obj.best_trial.number == trial.number:
                stale_trials = 0
            else:
                stale_trials += 1
            if early_stop and stale_trials >= early_stop:
                study_obj.stop()

        study.optimize(
            lambda trial: self._objective(trial, data),
            n_trials=epochs,
            n_jobs=jobs,
            callbacks=[callback],
        )

        completed_trials = [
            trial
            for trial in study.trials
            if trial.state == optuna.trial.TrialState.COMPLETE
        ]
        ranked_trials = sorted(completed_trials, key=lambda trial: trial.value)
        top_results = [
            self._trial_result_from_optuna(trial)
            for trial in ranked_trials[: max(top_n, 1)]
        ]
        best_result = self._trial_result_from_optuna(study.best_trial)
        merged_config = self._apply_params(best_result.params)

        return HyperoptSummary(
            strategy=self.strategy_name,
            loss_function=self.loss_name,
            spaces=self.spaces,
            best=best_result,
            top_results=top_results,
            merged_config=merged_config,
        )

    def export_merged_config(self, params: Dict[str, Any], output_path: str) -> None:
        merged_config = self._apply_params(params)
        export_path = Path(output_path)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with export_path.open("w", encoding="utf-8") as handle:
            json.dump(merged_config, handle, indent=2)

    def _objective(self, trial: Any, data: Dict[str, Any]) -> float:
        params = {
            parameter.name: parameter.suggest(trial)
            for parameter in self.selected_parameters
        }
        trial_config = self._apply_params(params)
        strategy = self._build_strategy(trial_config)
        result = asyncio.run(self._run_backtest(strategy, trial_config, data))
        loss = self._score_result(result)
        trial_result = HyperoptTrialResult.from_backtest(
            trial.number, loss, params, result
        )
        trial.set_user_attr("summary", trial_result.to_dict())
        return loss

    async def _run_backtest(
        self,
        strategy: BaseStrategy,
        trial_config: Dict[str, Any],
        data: Dict[str, Any],
    ) -> BacktestResult:
        backtest_config = copy.deepcopy(trial_config.get("backtesting", {}))
        backtest_config["risk_management"] = copy.deepcopy(
            trial_config.get("risk_management", {})
        )
        backtest_config["max_open_trades"] = trial_config.get("trading", {}).get(
            "max_open_trades", 3
        )
        engine = BacktestEngine(backtest_config)
        symbols = trial_config.get("trading", {}).get("pairs", [])
        return await engine.run_backtest(strategy, data, symbols)

    def _score_result(self, result: BacktestResult) -> float:
        if result.total_trades < self.min_trades:
            shortfall = self.min_trades - result.total_trades
            return 1_000_000 + shortfall * 1_000 - result.total_pnl_percentage
        return self.loss_function(result)

    def _build_strategy(self, trial_config: Dict[str, Any]) -> BaseStrategy:
        init_config = self.strategy_class.get_strategy_init_config(trial_config)
        return self.strategy_class(init_config)

    def _apply_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        config = self.strategy_class.apply_hyperopt_parameters_to_config(
            self.base_config, params
        )

        risk_config = copy.deepcopy(config.get("risk_management", {}))
        trading_config = copy.deepcopy(config.get("trading", {}))

        if "stop_loss_percent" in params:
            risk_config["stop_loss_percent"] = params["stop_loss_percent"]
        if "take_profit_percent" in params:
            risk_config["take_profit_percent"] = params["take_profit_percent"]
        if "trailing_stop" in params:
            risk_config["trailing_stop"] = params["trailing_stop"]
        if "trailing_stop_positive" in params:
            risk_config["trailing_stop_positive"] = params["trailing_stop_positive"]
        if "max_open_trades" in params:
            trading_config["max_open_trades"] = int(params["max_open_trades"])

        config["risk_management"] = risk_config
        config["trading"] = trading_config
        return config

    def _collect_parameters(self) -> List[HyperParameter]:
        parameters = list(self.strategy_class.hyperopt_parameters())
        parameters.extend(self._global_parameters())
        return parameters

    def _global_parameters(self) -> List[HyperParameter]:
        risk_config = self.base_config.get("risk_management", {})
        trading_config = self.base_config.get("trading", {})

        return [
            DecimalParameter(
                name="stop_loss_percent",
                default=float(risk_config.get("stop_loss_percent", 0.02)),
                low=0.005,
                high=0.05,
                step=0.005,
                space="stoploss",
            ),
            DecimalParameter(
                name="take_profit_percent",
                default=float(risk_config.get("take_profit_percent", 0.04)),
                low=0.01,
                high=0.12,
                step=0.005,
                space="roi",
            ),
            BooleanParameter(
                name="trailing_stop",
                default=bool(risk_config.get("trailing_stop", True)),
                space="trailing",
            ),
            DecimalParameter(
                name="trailing_stop_positive",
                default=float(risk_config.get("trailing_stop_positive", 0.01)),
                low=0.005,
                high=0.05,
                step=0.005,
                space="trailing",
            ),
            IntParameter(
                name="max_open_trades",
                default=int(trading_config.get("max_open_trades", 3)),
                low=1,
                high=6,
                step=1,
                space="trades",
            ),
        ]

    def _trial_result_from_optuna(self, trial: Any) -> HyperoptTrialResult:
        summary = trial.user_attrs.get("summary")
        if not summary:
            raise ValueError("Completed Optuna trial is missing summary metadata")
        return HyperoptTrialResult(**summary)


def available_strategies() -> List[str]:
    return sorted(STRATEGY_REGISTRY)


def _load_optuna() -> Any:
    try:
        import optuna
    except ImportError as exc:
        raise ImportError(
            "Optuna is required for hyperopt. Install the optional dependencies with "
            "'pip install -r requirements-hyperopt.txt'."
        ) from exc
    return optuna
