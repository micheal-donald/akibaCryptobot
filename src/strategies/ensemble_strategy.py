"""
Ensemble Strategy - Combines Momentum and FreqAI strategies for robust signals.

Uses weighted voting to combine:
- MomentumStrategy (40%): Technical indicator based
- MLFreqAIStrategy (60%): LightGBM machine learning based
"""

import logging
from datetime import datetime
from typing import Dict, Optional, List, Tuple
import pandas as pd
from dataclasses import dataclass
import sys
import os

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from strategies.base_strategy import BaseStrategy, TradingSignal, SignalType
from strategies.momentum_strategy import MomentumStrategy
from strategies.freqai.ml_freqai_strategy import MLFreqAIStrategy

logger = logging.getLogger(f"akiba_crypto_bot.{__name__}")


@dataclass
class EnsembleSignal:
    """Signal from ensemble of strategies"""

    signal_type: SignalType
    strength: float
    stop_loss: float
    take_profit: float
    metadata: Dict
    component_signals: List[Tuple[str, TradingSignal, float]]  # (name, signal, weight)


class EnsembleStrategy(BaseStrategy):
    """
    Ensemble strategy that combines multiple strategies using weighted voting.

    Default weights:
    - Momentum: 40% (traditional technical analysis)
    - FreqAI ML: 60% (machine learning predictions)

    The ensemble approach provides:
    - More robust signals by combining different approaches
    - Reduced false positives through consensus
    - Adaptability through ML while maintaining TA grounding
    """

    def __init__(self, config: Dict):
        super().__init__(config)

        # Get ensemble config
        ensemble_config = config.get("ensemble", {})

        # Initialize component strategies with their weights
        self.strategies: List[Tuple[BaseStrategy, float, str]] = []

        # Use strategy-specific config for sub-strategies
        strat_config = config.get("strategy_config", {})

        # Momentum strategy (40% weight)
        momentum_weight = ensemble_config.get("momentum_weight", 0.4)
        self.momentum = MomentumStrategy(strat_config)
        self.strategies.append((self.momentum, momentum_weight, "momentum"))

        # FreqAI ML strategy (60% weight)
        freqai_weight = ensemble_config.get("freqai_weight", 0.6)
        self.freqai = MLFreqAIStrategy(
            config
        )  # FreqAI needs full config for its sub-keys
        self.strategies.append((self.freqai, freqai_weight, "freqai"))

        # Voting thresholds
        self.min_consensus = ensemble_config.get(
            "min_consensus", 0.3
        )  # Min weighted vote to trigger
        self.min_strategies_agree = ensemble_config.get(
            "min_strategies_agree", 1
        )  # Min strategies that must agree

        # Signal strength adjustment
        self.strength_boost_on_consensus = ensemble_config.get("strength_boost", 0.1)

        logger.info(
            f"EnsembleStrategy initialized with weights: "
            f"momentum={momentum_weight}, freqai={freqai_weight}"
        )

    @classmethod
    def hyperopt_parameters(cls):
        from optimization.parameters import DecimalParameter, IntParameter

        return [
            DecimalParameter(
                name="momentum_weight",
                default=0.4,
                low=0.1,
                high=0.9,
                step=0.05,
                space="buy",
            ),
            DecimalParameter(
                name="freqai_weight",
                default=0.6,
                low=0.1,
                high=0.9,
                step=0.05,
                space="buy",
            ),
            DecimalParameter(
                name="min_consensus",
                default=0.3,
                low=0.1,
                high=0.7,
                step=0.05,
                space="buy",
            ),
            IntParameter(
                name="min_strategies_agree",
                default=1,
                low=1,
                high=2,
                step=1,
                space="buy",
            ),
            DecimalParameter(
                name="strength_boost",
                default=0.1,
                low=0.0,
                high=0.3,
                step=0.05,
                space="buy",
            ),
        ]

    @classmethod
    def apply_hyperopt_parameters_to_config(
        cls, full_config: Dict, params: Dict
    ) -> Dict:
        updated = full_config.copy()
        updated["strategy_config"] = dict(updated.get("strategy_config", {}))
        ensemble_config = dict(updated.get("ensemble", {}))

        if "momentum_weight" in params:
            ensemble_config["momentum_weight"] = params["momentum_weight"]
        if "freqai_weight" in params:
            ensemble_config["freqai_weight"] = params["freqai_weight"]

        if "momentum_weight" in ensemble_config or "freqai_weight" in ensemble_config:
            momentum_weight = float(ensemble_config.get("momentum_weight", 0.4))
            freqai_weight = float(ensemble_config.get("freqai_weight", 0.6))
            total_weight = momentum_weight + freqai_weight
            if total_weight > 0:
                ensemble_config["momentum_weight"] = momentum_weight / total_weight
                ensemble_config["freqai_weight"] = freqai_weight / total_weight

        for key in ("min_consensus", "min_strategies_agree", "strength_boost"):
            if key in params:
                ensemble_config[key] = params[key]

        updated["ensemble"] = ensemble_config
        return updated

    @classmethod
    def get_strategy_init_config(cls, full_config: Dict) -> Dict:
        return full_config.copy()

    def calculate_indicators(self, ohlcv_data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate indicators using the primary strategy (momentum).
        For ensemble, individual strategies handle their own indicators.
        """
        return self.momentum.calculate_indicators(ohlcv_data)

    async def generate_signal(
        self, symbol: str, ohlcv_data: pd.DataFrame, current_price: float
    ) -> Optional[TradingSignal]:
        """
        Generate trading signal by combining signals from all strategies.

        Uses weighted voting where:
        - BUY signals contribute positive weight
        - SELL signals contribute negative weight
        - HOLD signals contribute nothing
        """
        try:
            component_signals = []
            buy_weight = 0.0
            sell_weight = 0.0
            buy_count = 0
            sell_count = 0

            # Unavailable ML is different from a healthy component voting HOLD.
            # Redistribute its weight so the documented momentum fallback can
            # meet consensus and downstream signal-strength thresholds.
            available_strategies = [
                (strategy, weight, name)
                for strategy, weight, name in self.strategies
                if weight > 0
                and (
                    not hasattr(strategy, "is_available_for_symbol")
                    or strategy.is_available_for_symbol(symbol)
                )
            ]
            available_weight = sum(w for _, w, _ in available_strategies)
            if available_weight <= 0:
                return None
            total_weight = sum(w for _, w, _ in self.strategies)
            weight_scale = total_weight / available_weight

            # Collect signals from all strategies
            for strategy, weight, name in available_strategies:
                weight *= weight_scale
                try:
                    signal = await strategy.generate_signal(
                        symbol, ohlcv_data, current_price
                    )

                    if signal:
                        component_signals.append((name, signal, weight))
                        logger.debug(
                            f"  {name} → {signal.signal_type.name} "
                            f"(strength={signal.strength:.2f}, weight={weight})"
                        )

                        if signal.signal_type == SignalType.BUY:
                            buy_weight += weight * signal.strength
                            buy_count += 1
                        elif signal.signal_type == SignalType.SELL:
                            sell_weight += weight * signal.strength
                            sell_count += 1
                    else:
                        # No signal = effective HOLD
                        component_signals.append((name, None, weight))
                        logger.debug(f"  {name} → HOLD (no signal)")

                except Exception as e:
                    logger.warning(f"Error getting signal from {name}: {e}")
                    component_signals.append((name, None, weight))

            # Determine final signal through voting
            net_signal = buy_weight - sell_weight

            # Check if we have enough consensus
            if abs(net_signal) < self.min_consensus:
                logger.debug(f"No consensus for {symbol}: net_signal={net_signal:.3f}")
                return None

            # Determine signal type
            if net_signal > 0 and buy_count >= self.min_strategies_agree:
                signal_type = SignalType.BUY
                base_strength = buy_weight / sum(w for _, w, _ in self.strategies)
                agreeing_signals = [
                    (n, s)
                    for n, s, _ in component_signals
                    if s and s.signal_type == SignalType.BUY
                ]
            elif net_signal < 0 and sell_count >= self.min_strategies_agree:
                signal_type = SignalType.SELL
                base_strength = sell_weight / sum(w for _, w, _ in self.strategies)
                agreeing_signals = [
                    (n, s)
                    for n, s, _ in component_signals
                    if s and s.signal_type == SignalType.SELL
                ]
            else:
                return None

            # Boost strength if multiple strategies agree
            if len(agreeing_signals) > 1:
                base_strength = min(
                    1.0, base_strength + self.strength_boost_on_consensus
                )

            # Calculate stop loss and take profit from agreeing signals
            stop_losses = [s.stop_loss for _, s in agreeing_signals if s.stop_loss]
            take_profits = [s.take_profit for _, s in agreeing_signals if s.take_profit]

            # Use most conservative (tightest) stop loss
            if signal_type == SignalType.BUY:
                stop_loss = max(stop_losses) if stop_losses else current_price * 0.98
                take_profit = (
                    min(take_profits) if take_profits else current_price * 1.04
                )
            else:
                stop_loss = min(stop_losses) if stop_losses else current_price * 1.02
                take_profit = (
                    max(take_profits) if take_profits else current_price * 0.96
                )

            # Create ensemble signal
            signal = TradingSignal(
                signal_type=signal_type,
                strength=base_strength,
                price=current_price,
                timestamp=datetime.now(),
                stop_loss=stop_loss,
                take_profit=take_profit,
                metadata={
                    "strategy": "ensemble",
                    "component_signals": {
                        n: str(s.signal_type) if s else "HOLD"
                        for n, s, _ in component_signals
                    },
                    "buy_weight": buy_weight,
                    "sell_weight": sell_weight,
                    "net_signal": net_signal,
                    "agreeing_strategies": [n for n, _ in agreeing_signals],
                    "available_strategies": [n for _, _, n in available_strategies],
                },
            )

            logger.info(
                f"Ensemble signal for {symbol}: {signal_type.name} "
                f"(strength={base_strength:.2f}, sources={[n for n, _ in agreeing_signals]})"
            )

            return signal

        except Exception as e:
            logger.error(f"Error generating ensemble signal for {symbol}: {e}")
            return None

    async def should_exit_position(
        self,
        symbol: str,
        position: Dict,
        current_price: float,
        ohlcv_data: pd.DataFrame,
    ) -> Optional[TradingSignal]:
        """
        Check if any strategy suggests exiting the position.
        Exit if majority of strategies agree.
        """
        try:
            exit_weight = 0.0
            exit_count = 0

            for strategy, weight, name in self.strategies:
                try:
                    exit_signal = await strategy.should_exit_position(
                        symbol, position, current_price, ohlcv_data
                    )
                    if exit_signal:
                        exit_weight += weight
                        exit_count += 1
                except Exception as e:
                    logger.warning(f"Error checking exit from {name}: {e}")

            # Exit if majority agrees
            if exit_weight >= 0.5 or exit_count >= len(self.strategies) / 2:
                return TradingSignal(
                    signal_type=(
                        SignalType.SELL
                        if position.get("side") == "buy"
                        else SignalType.BUY
                    ),
                    strength=exit_weight,
                    price=current_price,
                    timestamp=datetime.now(),
                    stop_loss=None,
                    take_profit=None,
                    metadata={
                        "exit_reason": "ensemble_exit",
                        "exit_weight": exit_weight,
                    },
                )

            return None

        except Exception as e:
            logger.error(f"Error checking exit for {symbol}: {e}")
            return None

    async def train_models(self, symbol: str, ohlcv_data: pd.DataFrame):
        """Train all trainable strategies in the ensemble"""
        for strategy, _, name in self.strategies:
            if hasattr(strategy, "train_model"):
                try:
                    logger.info(f"Training {name} model for {symbol}")
                    await strategy.train_model(symbol, ohlcv_data)
                except Exception as e:
                    logger.error(f"Error training {name}: {e}")

    def load_models(self, models_dir: str = "models") -> int:
        """
        Load pre-trained models from disk for all ML-based strategies.

        For each component strategy that exposes
        ``load_model_for_symbol(symbol, filepath)``, this routes every
        discovered ``.joblib`` file through the per-symbol registry using
        the strategy's own :meth:`symbol_from_model_filename` classmethod
        (so each strategy owns its filename convention — the ensemble does
        not hardcode one).

        Strategies without ``load_model_for_symbol`` fall back to the
        legacy ``load_model(filepath)`` single-slot path, which overwrites
        on each call. That fallback is kept only for backwards
        compatibility; any ML strategy that holds more than one model
        should implement ``load_model_for_symbol``.

        Returns:
            Number of models successfully loaded.
        """

        loaded = 0

        if not os.path.exists(models_dir):
            logger.warning(f"Models directory not found: {models_dir}")
            return loaded

        model_files = [f for f in os.listdir(models_dir) if f.endswith(".joblib")]
        if not model_files:
            logger.warning(f"No pre-trained model files found in {models_dir}")
            return loaded

        for strategy, _, name in self.strategies:
            supports_registry = hasattr(strategy, "load_model_for_symbol")
            supports_legacy = hasattr(strategy, "load_model")
            if not (supports_registry or supports_legacy):
                continue

            parser = getattr(strategy, "symbol_from_model_filename", None)

            for model_file in model_files:
                filepath = os.path.join(models_dir, model_file)
                symbol = parser(model_file) if callable(parser) else None

                try:
                    if supports_registry and symbol is not None:
                        strategy.load_model_for_symbol(symbol, filepath)
                        logger.info(
                            f"✅ Loaded pre-trained model: {model_file} → "
                            f"{name} ({symbol})"
                        )
                    elif supports_legacy:
                        # Legacy single-slot fallback — either the strategy
                        # doesn't support the registry yet, or the filename
                        # doesn't match the strategy's convention.
                        strategy.load_model(filepath)
                        logger.info(
                            f"✅ Loaded pre-trained model (single-slot): "
                            f"{model_file} → {name}"
                        )
                    else:
                        continue
                    loaded += 1
                except Exception as e:
                    logger.error(f"Failed to load {model_file}: {e}")

        if loaded > 0:
            logger.info(f"FreqAI activated: {loaded} model(s) loaded from {models_dir}")
        else:
            logger.warning(
                "No FreqAI models loaded. ML predictions will be inactive. "
                "Run 'python scripts/train_models.py' or trigger the GitHub Actions training workflow."
            )

        return loaded

    def get_strategy_status(self) -> Dict:
        """Get status of all component strategies"""
        status = {}
        for strategy, weight, name in self.strategies:
            status[name] = {
                "weight": weight,
                "type": type(strategy).__name__,
                "trained": getattr(strategy, "model_trained", "N/A"),
            }
        return status
