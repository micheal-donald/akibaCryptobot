"""Freqtrade-style hyperopt parameter primitives for AkibaCryptoBot."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, List, Sequence

DEFAULT_SPACES = ("buy", "sell", "roi", "stoploss")


@dataclass(frozen=True)
class HyperParameter:
    """Base hyperparameter definition."""

    name: str
    default: Any
    space: str = "buy"
    optimize: bool = True
    load: bool = True

    def suggest(self, trial: Any) -> Any:
        raise NotImplementedError


@dataclass(frozen=True)
class IntParameter(HyperParameter):
    low: int = 0
    high: int = 1
    step: int = 1

    def suggest(self, trial: Any) -> int:
        return int(trial.suggest_int(self.name, self.low, self.high, step=self.step))


@dataclass(frozen=True)
class DecimalParameter(HyperParameter):
    low: float = 0.0
    high: float = 1.0
    step: float = 0.001

    def suggest(self, trial: Any) -> float:
        return float(
            trial.suggest_float(self.name, self.low, self.high, step=self.step)
        )


@dataclass(frozen=True)
class CategoricalParameter(HyperParameter):
    choices: Sequence[Any] = field(default_factory=tuple)

    def suggest(self, trial: Any) -> Any:
        return trial.suggest_categorical(self.name, list(self.choices))


@dataclass(frozen=True)
class BooleanParameter(CategoricalParameter):
    choices: Sequence[Any] = field(default_factory=lambda: (True, False))


def resolve_spaces(
    requested_spaces: Iterable[str] | None,
    available_spaces: Iterable[str],
) -> List[str]:
    """Resolve Freqtrade-like spaces into a normalized ordered list."""

    normalized = [space.strip() for space in (requested_spaces or ["default"]) if space]
    available = list(dict.fromkeys(available_spaces))

    if not normalized:
        normalized = ["default"]

    resolved: List[str] = []
    for space in normalized:
        if space == "all":
            for item in available:
                if item not in resolved:
                    resolved.append(item)
        elif space == "default":
            for item in DEFAULT_SPACES:
                if item in available and item not in resolved:
                    resolved.append(item)
        elif space in available and space not in resolved:
            resolved.append(space)

    return resolved or [space for space in DEFAULT_SPACES if space in available]


def select_parameters(
    parameters: Iterable[HyperParameter],
    spaces: Iterable[str],
) -> List[HyperParameter]:
    """Return optimized parameters belonging to the requested spaces."""

    selected_spaces = set(spaces)
    return [
        parameter
        for parameter in parameters
        if parameter.optimize and parameter.space in selected_spaces
    ]
