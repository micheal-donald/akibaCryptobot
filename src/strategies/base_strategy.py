from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple, Any
import pandas as pd
import logging
from datetime import datetime
from enum import Enum
import importlib

logger = logging.getLogger(__name__)


class SignalType(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"


class TradingSignal:
    def __init__(self, signal_type: SignalType, strength: float, 
                 price: float, timestamp: datetime, 
                 stop_loss: Optional[float] = None,
                 take_profit: Optional[float] = None,
                 metadata: Optional[Dict] = None):
        self.signal_type = signal_type
        self.strength = strength  # 0.0 to 1.0
        self.price = price
        self.timestamp = timestamp
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.metadata = metadata or {}

    def __str__(self):
        return f"TradingSignal({self.signal_type.value}, strength={self.strength:.2f}, price={self.price:.6f})"


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.name = self.__class__.__name__
        self.timeframe = config.get('timeframe', '1h')
        self.lookback_period = config.get('lookback_period', 100)
        
        # Strategy parameters
        self.min_signal_strength = config.get('min_signal_strength', 0.6)
        self.stop_loss_percent = config.get('stop_loss_percent', 0.02)
        self.take_profit_percent = config.get('take_profit_percent', 0.04)
        
        # State tracking
        self.last_signal = None
        self.last_update = None
        self.performance_metrics = {
            'total_signals': 0,
            'successful_signals': 0,
            'win_rate': 0.0,
            'total_pnl': 0.0
        }
        
        logger.info(f"Initialized strategy: {self.name}")

    @abstractmethod
    async def generate_signal(self, symbol: str, ohlcv_data: pd.DataFrame, 
                            current_price: float) -> Optional[TradingSignal]:
        """
        Generate trading signal based on market data
        
        Args:
            symbol: Trading pair symbol
            ohlcv_data: Historical OHLCV data
            current_price: Current market price
            
        Returns:
            TradingSignal or None if no signal
        """
        pass

    @abstractmethod
    def calculate_indicators(self, ohlcv_data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate technical indicators for the strategy
        
        Args:
            ohlcv_data: Historical OHLCV data
            
        Returns:
            DataFrame with calculated indicators
        """
        pass

    def validate_signal(self, signal: TradingSignal) -> bool:
        """
        Validate if a signal meets minimum criteria
        
        Args:
            signal: TradingSignal to validate
            
        Returns:
            True if signal is valid
        """
        if signal.strength < self.min_signal_strength:
            logger.debug(f"Signal strength {signal.strength:.2f} below threshold {self.min_signal_strength}")
            return False
        
        return True

    async def should_exit_position(self, symbol: str, current_position: Dict, 
                                 current_price: float, ohlcv_data: pd.DataFrame) -> Optional[TradingSignal]:
        """
        Determine if current position should be exited
        
        Args:
            symbol: Trading pair symbol
            current_position: Current position info
            current_price: Current market price
            ohlcv_data: Historical OHLCV data
            
        Returns:
            Exit signal or None
        """
        # Basic exit logic - can be overridden by specific strategies
        entry_price = current_position.get('entry_price')
        side = current_position.get('side')
        
        if not entry_price or not side:
            return None
        
        # Calculate current PnL percentage
        if side == 'buy':
            pnl_percent = (current_price - entry_price) / entry_price
        else:
            pnl_percent = (entry_price - current_price) / entry_price
        
        # Check stop loss
        if pnl_percent <= -self.stop_loss_percent:
            return TradingSignal(
                signal_type=SignalType.CLOSE_LONG if side == 'buy' else SignalType.CLOSE_SHORT,
                strength=1.0,
                price=current_price,
                timestamp=datetime.now(),
                metadata={'exit_reason': 'stop_loss', 'pnl_percent': pnl_percent}
            )
        
        # Check take profit
        if pnl_percent >= self.take_profit_percent:
            return TradingSignal(
                signal_type=SignalType.CLOSE_LONG if side == 'buy' else SignalType.CLOSE_SHORT,
                strength=1.0,
                price=current_price,
                timestamp=datetime.now(),
                metadata={'exit_reason': 'take_profit', 'pnl_percent': pnl_percent}
            )
        
        return None

    def calculate_position_size(self, balance: float, signal_strength: float, 
                              current_price: float) -> float:
        """
        Calculate position size based on signal strength and risk management
        
        Args:
            balance: Available balance
            signal_strength: Signal strength (0.0 to 1.0)
            current_price: Current market price
            
        Returns:
            Position size
        """
        base_position_size = balance * self.config.get('base_position_size', 0.05)
        
        # Adjust position size based on signal strength
        adjusted_size = base_position_size * signal_strength
        
        # Convert to quantity
        quantity = adjusted_size / current_price
        
        return quantity

    def update_performance(self, signal: TradingSignal, outcome: Dict):
        """
        Update strategy performance metrics
        
        Args:
            signal: The trading signal that was executed
            outcome: Outcome of the trade (pnl, success, etc.)
        """
        self.performance_metrics['total_signals'] += 1
        
        if outcome.get('success', False):
            self.performance_metrics['successful_signals'] += 1
        
        pnl = outcome.get('pnl', 0)
        self.performance_metrics['total_pnl'] += pnl
        
        # Calculate win rate
        if self.performance_metrics['total_signals'] > 0:
            self.performance_metrics['win_rate'] = (
                self.performance_metrics['successful_signals'] / 
                self.performance_metrics['total_signals']
            )
        
        logger.info(f"Strategy {self.name} performance updated: "
                   f"Win rate: {self.performance_metrics['win_rate']:.2%}, "
                   f"Total PnL: {self.performance_metrics['total_pnl']:.6f}")

    def get_performance_summary(self) -> Dict:
        """Get strategy performance summary"""
        return {
            'strategy_name': self.name,
            'total_signals': self.performance_metrics['total_signals'],
            'successful_signals': self.performance_metrics['successful_signals'],
            'win_rate': self.performance_metrics['win_rate'],
            'total_pnl': self.performance_metrics['total_pnl'],
            'last_signal': str(self.last_signal) if self.last_signal else None,
            'last_update': self.last_update.isoformat() if self.last_update else None
        }

    def reset_performance(self):
        """Reset performance metrics"""
        self.performance_metrics = {
            'total_signals': 0,
            'successful_signals': 0,
            'win_rate': 0.0,
            'total_pnl': 0.0
        }
        logger.info(f"Performance metrics reset for strategy {self.name}")

    async def warmup(self, symbol: str, ohlcv_data: pd.DataFrame):
        """
        Warmup strategy with historical data
        
        Args:
            symbol: Trading pair symbol
            ohlcv_data: Historical data for warmup
        """
        logger.info(f"Warming up strategy {self.name} for {symbol}")
        
        # Calculate indicators with historical data
        indicators_data = self.calculate_indicators(ohlcv_data)
        
        # This can be overridden by specific strategies for more complex warmup
        logger.info(f"Strategy {self.name} warmup completed for {symbol}")

    def get_required_history(self) -> int:
        """
        Get the minimum number of historical periods required
        
        Returns:
            Number of periods needed
        """
        return max(self.lookback_period, 200)  # Default minimum

    @property
    def is_ready(self) -> bool:
        """Check if strategy is ready to generate signals"""
        return True  # Override in specific strategies if needed

    def log_signal(self, signal: TradingSignal, symbol: str):
        """Log generated signal"""
        if signal:
            logger.info(f"Generated signal for {symbol}: {signal}")
            self.last_signal = signal
            self.last_update = datetime.now()
        else:
            logger.debug(f"No signal generated for {symbol}")


class StrategyManager:
    """Manager for multiple trading strategies"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.strategies = {}
        self.active_strategy = None
        
    def add_strategy(self, strategy: BaseStrategy, name: str = None):
        """Add a strategy to the manager"""
        strategy_name = name or strategy.name
        self.strategies[strategy_name] = strategy
        logger.info(f"Added strategy: {strategy_name}")
        
        if not self.active_strategy:
            self.active_strategy = strategy_name
            logger.info(f"Set {strategy_name} as active strategy")
            
    def add_strategy_by_name(self, strategy_name: str, config: Dict):
        """Dynamically load and add a strategy by name"""
        try:
            # Handle FreqAI strategies
            if strategy_name.startswith('freqai_'):
                module_name = f"strategies.freqai.{strategy_name.replace('freqai_', '')}"
            else:
                module_name = f"strategies.{strategy_name.lower()}_strategy"
                
            # Import the module
            module = importlib.import_module(module_name)
            
            # Get the strategy class (assuming it matches the module name)
            class_name = ''.join(word.capitalize() for word in strategy_name.split('_'))
            if strategy_name.startswith('freqai_'):
                class_name = 'FreqAI' + class_name.replace('Freqai', '')
            else:
                class_name = class_name + 'Strategy'
                
            strategy_class = getattr(module, class_name)
            
            # Create and add the strategy
            strategy_instance = strategy_class(config)
            self.add_strategy(strategy_instance, strategy_name)
            
        except Exception as e:
            logger.error(f"Failed to load strategy {strategy_name}: {e}")
            
    def set_active_strategy(self, strategy_name: str):
        """Set the active strategy"""
        if strategy_name in self.strategies:
            self.active_strategy = strategy_name
            logger.info(f"Switched to strategy: {strategy_name}")
        else:
            logger.error(f"Strategy {strategy_name} not found")

    def get_active_strategy(self) -> Optional[BaseStrategy]:
        """Get the currently active strategy"""
        if self.active_strategy and self.active_strategy in self.strategies:
            return self.strategies[self.active_strategy]
        return None

    async def generate_signal(self, symbol: str, ohlcv_data: pd.DataFrame, 
                            current_price: float) -> Optional[TradingSignal]:
        """Generate signal using active strategy"""
        strategy = self.get_active_strategy()
        if strategy:
            return await strategy.generate_signal(symbol, ohlcv_data, current_price)
        return None

    def get_all_performance(self) -> Dict:
        """Get performance summary for all strategies"""
        return {
            name: strategy.get_performance_summary() 
            for name, strategy in self.strategies.items()
        }