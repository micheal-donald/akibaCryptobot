"""
Ensemble Strategy - Combines Momentum and FreqAI strategies for robust signals.

Uses weighted voting to combine:
- MomentumStrategy (40%): Technical indicator based
- MLFreqAIStrategy (60%): LightGBM machine learning based
"""

import logging
from typing import Dict, Optional, List, Tuple
import pandas as pd
import numpy as np
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

logger = logging.getLogger(__name__)


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
        ensemble_config = config.get('ensemble', {})
        
        # Initialize component strategies with their weights
        self.strategies: List[Tuple[BaseStrategy, float, str]] = []
        
        # Momentum strategy (40% weight)
        momentum_weight = ensemble_config.get('momentum_weight', 0.4)
        self.momentum = MomentumStrategy(config)
        self.strategies.append((self.momentum, momentum_weight, 'momentum'))
        
        # FreqAI ML strategy (60% weight)
        freqai_weight = ensemble_config.get('freqai_weight', 0.6)
        self.freqai = MLFreqAIStrategy(config)
        self.strategies.append((self.freqai, freqai_weight, 'freqai'))
        
        # Voting thresholds
        self.min_consensus = ensemble_config.get('min_consensus', 0.5)  # Min weighted vote to trigger
        self.min_strategies_agree = ensemble_config.get('min_strategies_agree', 1)  # Min strategies that must agree
        
        # Signal strength adjustment
        self.strength_boost_on_consensus = ensemble_config.get('strength_boost', 0.1)
        
        logger.info(f"EnsembleStrategy initialized with weights: "
                   f"momentum={momentum_weight}, freqai={freqai_weight}")
    
    def calculate_indicators(self, ohlcv_data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate indicators using the primary strategy (momentum).
        For ensemble, individual strategies handle their own indicators.
        """
        return self.momentum.calculate_indicators(ohlcv_data)
    
    async def generate_signal(
        self, 
        symbol: str, 
        ohlcv_data: pd.DataFrame, 
        current_price: float
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
            
            # Collect signals from all strategies
            for strategy, weight, name in self.strategies:
                try:
                    signal = await strategy.generate_signal(symbol, ohlcv_data, current_price)
                    
                    if signal:
                        component_signals.append((name, signal, weight))
                        
                        if signal.signal_type == SignalType.BUY:
                            buy_weight += weight * signal.strength
                            buy_count += 1
                        elif signal.signal_type == SignalType.SELL:
                            sell_weight += weight * signal.strength
                            sell_count += 1
                    else:
                        # No signal = effective HOLD
                        component_signals.append((name, None, weight))
                        
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
                base_strength = buy_weight / sum(w for _, _, w in self.strategies)
                agreeing_signals = [(n, s) for n, s, _ in component_signals if s and s.signal_type == SignalType.BUY]
            elif net_signal < 0 and sell_count >= self.min_strategies_agree:
                signal_type = SignalType.SELL
                base_strength = sell_weight / sum(w for _, _, w in self.strategies)
                agreeing_signals = [(n, s) for n, s, _ in component_signals if s and s.signal_type == SignalType.SELL]
            else:
                return None
            
            # Boost strength if multiple strategies agree
            if len(agreeing_signals) > 1:
                base_strength = min(1.0, base_strength + self.strength_boost_on_consensus)
            
            # Calculate stop loss and take profit from agreeing signals
            stop_losses = [s.stop_loss for _, s in agreeing_signals if s.stop_loss]
            take_profits = [s.take_profit for _, s in agreeing_signals if s.take_profit]
            
            # Use most conservative (tightest) stop loss
            if signal_type == SignalType.BUY:
                stop_loss = max(stop_losses) if stop_losses else current_price * 0.98
                take_profit = min(take_profits) if take_profits else current_price * 1.04
            else:
                stop_loss = min(stop_losses) if stop_losses else current_price * 1.02
                take_profit = max(take_profits) if take_profits else current_price * 0.96
            
            # Create ensemble signal
            signal = TradingSignal(
                signal_type=signal_type,
                strength=base_strength,
                stop_loss=stop_loss,
                take_profit=take_profit,
                metadata={
                    'strategy': 'ensemble',
                    'component_signals': {n: str(s.signal_type) if s else 'HOLD' for n, s, _ in component_signals},
                    'buy_weight': buy_weight,
                    'sell_weight': sell_weight,
                    'net_signal': net_signal,
                    'agreeing_strategies': [n for n, _ in agreeing_signals]
                }
            )
            
            logger.info(f"Ensemble signal for {symbol}: {signal_type.name} "
                       f"(strength={base_strength:.2f}, sources={[n for n, _ in agreeing_signals]})")
            
            return signal
            
        except Exception as e:
            logger.error(f"Error generating ensemble signal for {symbol}: {e}")
            return None
    
    async def should_exit_position(
        self, 
        symbol: str, 
        position: Dict, 
        current_price: float, 
        ohlcv_data: pd.DataFrame
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
                    signal_type=SignalType.SELL if position.get('side') == 'buy' else SignalType.BUY,
                    strength=exit_weight,
                    stop_loss=None,
                    take_profit=None,
                    metadata={'exit_reason': 'ensemble_exit', 'exit_weight': exit_weight}
                )
            
            return None
            
        except Exception as e:
            logger.error(f"Error checking exit for {symbol}: {e}")
            return None
    
    async def train_models(self, symbol: str, ohlcv_data: pd.DataFrame):
        """Train all trainable strategies in the ensemble"""
        for strategy, _, name in self.strategies:
            if hasattr(strategy, 'train_model'):
                try:
                    logger.info(f"Training {name} model for {symbol}")
                    await strategy.train_model(symbol, ohlcv_data)
                except Exception as e:
                    logger.error(f"Error training {name}: {e}")
    
    def get_strategy_status(self) -> Dict:
        """Get status of all component strategies"""
        status = {}
        for strategy, weight, name in self.strategies:
            status[name] = {
                'weight': weight,
                'type': type(strategy).__name__,
                'trained': getattr(strategy, 'model_trained', 'N/A')
            }
        return status
