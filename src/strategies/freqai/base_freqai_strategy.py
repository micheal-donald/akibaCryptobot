import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime
import logging
import sys
import os

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from strategies.base_strategy import BaseStrategy, TradingSignal, SignalType
from indicators.technical_indicators import TechnicalIndicators

logger = logging.getLogger(__name__)


class BaseFreqAIStrategy(BaseStrategy):
    """
    Base class for FreqAI strategies that integrates with akibaCryptobot framework
    """
    
    def __init__(self, config: Dict):
        super().__init__(config)
        
        # FreqAI specific configuration
        self.freqai_config = config.get('freqai', {})
        self.freqai_enabled = self.freqai_config.get('enabled', False)
        self.freqai_model = self.freqai_config.get('model', 'LightGBMClassifier')
        self.freqai_train_period = self.freqai_config.get('train_period', 200)
        self.freqai_identifier = self.freqai_config.get('identifier', 'freqai_model')
        
        # Feature engineering parameters
        self.include_timeframes = self.freqai_config.get('include_timeframes', ['1h'])
        self.include_corr_pairs = self.freqai_config.get('include_corr_pairs', [])
        
        # Model management
        self.model = None
        self.data_pipeline = None
        self.is_model_trained = False
        
        logger.info(f"Initialized FreqAI strategy: {self.name}")
        logger.info(f"FreqAI enabled: {self.freqai_enabled}")
        logger.info(f"Model: {self.freqai_model}")
        
    def feature_engineering_expand_all(self, dataframe: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """
        Expand features across multiple timeframes
        This method is called by FreqAI to generate features for all timeframes
        """
        # This is a placeholder that should be overridden by specific strategies
        return dataframe
    
    def feature_engineering_standard(self, dataframe: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """
        Standard feature engineering that applies to a single timeframe
        This method is called by FreqAI to generate features for a single timeframe
        """
        # This is a placeholder that should be overridden by specific strategies
        return dataframe
    
    def set_freqai_targets(self, dataframe: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """
        Set targets for FreqAI model training
        This method is called by FreqAI to define what the model should predict
        """
        # This is a placeholder that should be overridden by specific strategies
        return dataframe
    
    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        """
        Populate indicators for the strategy
        This method is called by Freqtrade/FreqAI
        """
        # This is a placeholder that should be overridden by specific strategies
        return dataframe
    
    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        """
        Populate entry signals based on model predictions
        This method is called by Freqtrade/FreqAI
        """
        # This is a placeholder that should be overridden by specific strategies
        return dataframe
    
    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        """
        Populate exit signals based on model predictions
        This method is called by Freqtrade/FreqAI
        """
        # This is a placeholder that should be overridden by specific strategies
        return dataframe
    
    async def generate_signal(self, symbol: str, ohlcv_data: pd.DataFrame, 
                            current_price: float) -> Optional[TradingSignal]:
        """
        Generate trading signal based on FreqAI model predictions
        """
        try:
            if not self.freqai_enabled:
                logger.warning("FreqAI is not enabled for this strategy")
                return None
            
            if not self.is_model_trained:
                logger.warning("FreqAI model is not trained yet")
                return None
            
            if len(ohlcv_data) < self.lookback_period:
                logger.warning(f"Insufficient data for {symbol}: {len(ohlcv_data)} bars")
                return None
            
            # Generate features for prediction
            features_df = self._generate_features(ohlcv_data)
            
            # Get model prediction
            prediction = self._get_model_prediction(features_df)
            
            # Convert prediction to trading signal
            signal = self._prediction_to_signal(prediction, current_price)
            
            if signal and self.validate_signal(signal):
                self.log_signal(signal, symbol)
                return signal
            
            return None
            
        except Exception as e:
            logger.error(f"Error generating FreqAI signal for {symbol}: {e}")
            return None
    
    def _generate_features(self, ohlcv_data: pd.DataFrame) -> pd.DataFrame:
        """
        Generate features for model prediction
        """
        # Convert to the format expected by FreqAI
        dataframe = ohlcv_data.copy()
        dataframe.rename(columns={
            'open': 'open',
            'high': 'high', 
            'low': 'low',
            'close': 'close',
            'volume': 'volume'
        }, inplace=True)
        
        # Add technical indicators
        dataframe = self.calculate_indicators(dataframe)
        
        # Apply feature engineering
        dataframe = self.feature_engineering_standard(dataframe)
        
        return dataframe
    
    def _get_model_prediction(self, features_df: pd.DataFrame) -> Dict:
        """
        Get prediction from the trained model
        """
        # This is a placeholder - actual implementation would depend on the model type
        # and would interface with FreqAI's prediction system
        latest_features = features_df.iloc[-1:].dropna()
        
        if len(latest_features) == 0:
            return {'prediction': 0, 'confidence': 0.0}
        
        # Placeholder prediction logic
        # In a real implementation, this would call the actual FreqAI model
        prediction = np.random.choice([0, 1, 2])  # 0: hold, 1: buy, 2: sell
        confidence = np.random.random()  # Random confidence between 0 and 1
        
        return {
            'prediction': prediction,
            'confidence': confidence
        }
    
    def _prediction_to_signal(self, prediction: Dict, current_price: float) -> Optional[TradingSignal]:
        """
        Convert model prediction to trading signal
        """
        pred_value = prediction.get('prediction', 0)
        confidence = prediction.get('confidence', 0.0)
        
        if confidence < self.min_signal_strength:
            return None
        
        if pred_value == 1:  # Buy signal
            signal_type = SignalType.BUY
        elif pred_value == 2:  # Sell signal
            signal_type = SignalType.SELL
        else:  # Hold signal
            return None
        
        # Calculate stop loss and take profit
        stop_loss = self._calculate_stop_loss(current_price, signal_type)
        take_profit = self._calculate_take_profit(current_price, signal_type)
        
        return TradingSignal(
            signal_type=signal_type,
            strength=confidence,
            price=current_price,
            timestamp=datetime.now(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                'strategy': 'freqai',
                'confidence': confidence,
                'model_prediction': pred_value
            }
        )
    
    def _calculate_stop_loss(self, current_price: float, signal_type: SignalType) -> float:
        """
        Calculate dynamic stop loss based on ATR or percentage
        """
        # Use the base implementation from BaseStrategy
        if signal_type == SignalType.BUY:
            return current_price * (1 - self.stop_loss_percent)
        else:
            return current_price * (1 + self.stop_loss_percent)
    
    def _calculate_take_profit(self, current_price: float, signal_type: SignalType) -> float:
        """
        Calculate dynamic take profit based on ATR or percentage
        """
        # Use the base implementation from BaseStrategy
        if signal_type == SignalType.BUY:
            return current_price * (1 + self.take_profit_percent)
        else:
            return current_price * (1 - self.take_profit_percent)
    
    async def train_model(self, symbol: str, ohlcv_data: pd.DataFrame):
        """
        Train the FreqAI model
        """
        try:
            if not self.freqai_enabled:
                logger.warning("FreqAI is not enabled for this strategy")
                return
            
            logger.info(f"Training FreqAI model for {symbol}")
            
            # Prepare training data
            training_data = self._prepare_training_data(ohlcv_data)
            
            # In a real implementation, this would interface with FreqAI's training system
            # For now, we'll just set the flag to indicate the model is "trained"
            self.is_model_trained = True
            
            logger.info(f"FreqAI model training completed for {symbol}")
            
        except Exception as e:
            logger.error(f"Error training FreqAI model for {symbol}: {e}")
    
    def _prepare_training_data(self, ohlcv_data: pd.DataFrame) -> pd.DataFrame:
        """
        Prepare data for model training
        """
        # Convert to the format expected by FreqAI
        dataframe = ohlcv_data.copy()
        dataframe.rename(columns={
            'open': 'open',
            'high': 'high', 
            'low': 'low',
            'close': 'close',
            'volume': 'volume'
        }, inplace=True)
        
        # Add technical indicators
        dataframe = self.calculate_indicators(dataframe)
        
        # Set targets for training
        dataframe = self.set_freqai_targets(dataframe)
        
        # Apply feature engineering
        dataframe = self.feature_engineering_standard(dataframe)
        
        return dataframe
    
    def get_required_history(self) -> int:
        """
        Get minimum historical data required for this strategy
        """
        return max(self.lookback_period, self.freqai_train_period)
    
    def calculate_indicators(self, ohlcv_data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate technical indicators for the strategy
        This is a basic implementation that can be overridden by specific strategies
        """
        df = ohlcv_data.copy()
        
        # Add basic indicators
        df['rsi'] = TechnicalIndicators.rsi(df['close'])
        macd_data = TechnicalIndicators.macd(df['close'])
        df['macd'] = macd_data['macd']
        df['macd_signal'] = macd_data['macd_signal']
        
        return df