import pandas as pd
import numpy as np
from typing import Dict, Optional
from datetime import datetime
import logging
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import joblib
import sys
import os

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from strategies.freqai.base_freqai_strategy import BaseFreqAIStrategy, TradingSignal, SignalType
from indicators.technical_indicators import TechnicalIndicators

logger = logging.getLogger(__name__)


class MLFreqAIStrategy(BaseFreqAIStrategy):
    """
    Machine Learning FreqAI strategy that trains and uses ML models for predictions
    
    This strategy demonstrates how to integrate scikit-learn models with FreqAI
    """
    
    def __init__(self, config: Dict):
        super().__init__(config)
        
        # ML-specific parameters
        self.ml_config = self.freqai_config.get('ml_params', {})
        self.lookahead_period = self.ml_config.get('lookahead_period', 5)
        self.training_data_period = self.ml_config.get('training_data_period', 1000)
        self.feature_columns = []
        
        # Initialize ML model
        self.ml_model = None
        self.model_trained = False
        
        logger.info(f"MLFreqAIStrategy initialized with lookahead={self.lookahead_period}")
    
    def feature_engineering_standard(self, dataframe: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """
        Standard feature engineering for ML model
        """
        # Technical indicators as features
        dataframe['%-rsi'] = TechnicalIndicators.rsi(dataframe['close'], 14)
        dataframe['%-macd'] = TechnicalIndicators.macd(dataframe['close'])['macd']
        dataframe['%-macd_signal'] = TechnicalIndicators.macd(dataframe['close'])['macd_signal']
        
        # Bollinger Bands
        bb_data = TechnicalIndicators.bollinger_bands(dataframe['close'])
        dataframe['%-bb_width'] = bb_data['bb_width']
        dataframe['%-bb_percent'] = bb_data['bb_percent']
        
        # Price-based features
        dataframe['%-price_change_1'] = dataframe['close'].pct_change(1)
        dataframe['%-price_change_3'] = dataframe['close'].pct_change(3)
        dataframe['%-price_change_5'] = dataframe['close'].pct_change(5)
        dataframe['%-price_change_10'] = dataframe['close'].pct_change(10)
        
        # Moving averages
        dataframe['%-sma_10'] = TechnicalIndicators.sma(dataframe['close'], 10)
        dataframe['%-sma_20'] = TechnicalIndicators.sma(dataframe['close'], 20)
        dataframe['%-sma_50'] = TechnicalIndicators.sma(dataframe['close'], 50)
        
        # Price position relative to moving averages
        dataframe['%-close_vs_sma_10'] = (dataframe['close'] / dataframe['%-sma_10']) - 1
        dataframe['%-close_vs_sma_20'] = (dataframe['close'] / dataframe['%-sma_20']) - 1
        
        # Volume features
        dataframe['%-volume_change'] = dataframe['volume'].pct_change(1)
        dataframe['%-volume_sma_ratio'] = dataframe['volume'] / dataframe['volume'].rolling(20).mean()
        
        # Volatility
        dataframe['%-volatility_10'] = dataframe['close'].pct_change().rolling(10).std()
        dataframe['%-volatility_20'] = dataframe['close'].pct_change().rolling(20).std()
        
        # Time-based features
        dataframe['%-hour'] = pd.to_datetime(dataframe['date']).dt.hour
        dataframe['%-day_of_week'] = pd.to_datetime(dataframe['date']).dt.dayofweek
        
        return dataframe
    
    def set_freqai_targets(self, dataframe: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """
        Set targets for ML model training
        """
        # Classification target: 1 if price goes up in lookahead_period, 0 otherwise
        dataframe['&-price_up'] = (
            dataframe['close'].shift(-self.lookahead_period) > dataframe['close']
        ).astype(int)
        
        # Regression target: percentage change in price
        dataframe['&-price_change'] = (
            (dataframe['close'].shift(-self.lookahead_period) - dataframe['close']) / dataframe['close']
        )
        
        return dataframe
    
    def calculate_indicators(self, ohlcv_data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate technical indicators for the strategy
        """
        df = ohlcv_data.copy()
        
        # Basic indicators
        df['rsi'] = TechnicalIndicators.rsi(df['close'], 14)
        macd_data = TechnicalIndicators.macd(df['close'])
        df['macd'] = macd_data['macd']
        df['macd_signal'] = macd_data['macd_signal']
        
        # Moving averages
        df['sma_10'] = TechnicalIndicators.sma(df['close'], 10)
        df['sma_20'] = TechnicalIndicators.sma(df['close'], 20)
        
        return df
    
    async def train_model(self, symbol: str, ohlcv_data: pd.DataFrame):
        """
        Train the ML model using historical data
        """
        try:
            if not self.freqai_enabled:
                logger.warning("FreqAI is not enabled for this strategy")
                return
            
            logger.info(f"Training ML model for {symbol}")
            
            # Prepare training data
            training_data = self._prepare_training_data(ohlcv_data)
            
            # Extract features and targets
            feature_cols = [col for col in training_data.columns if col.startswith('%-')]
            target_col = '&-price_up'
            
            # Store feature column names for prediction
            self.feature_columns = feature_cols
            
            # Remove rows with NaN values
            clean_data = training_data.dropna()
            
            if len(clean_data) < 100:
                logger.warning(f"Insufficient clean data for training: {len(clean_data)} rows")
                return
            
            X = clean_data[feature_cols]
            y = clean_data[target_col]
            
            # Split data for training and validation
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42, stratify=y
            )
            
            # Initialize and train the model
            if self.freqai_model == 'RandomForest':
                self.ml_model = RandomForestClassifier(
                    n_estimators=100,
                    max_depth=10,
                    random_state=42
                )
            else:
                # Default to RandomForest
                self.ml_model = RandomForestClassifier(
                    n_estimators=100,
                    max_depth=10,
                    random_state=42
                )
            
            # Train the model
            self.ml_model.fit(X_train, y_train)
            
            # Evaluate the model
            y_pred = self.ml_model.predict(X_test)
            accuracy = accuracy_score(y_test, y_pred)
            
            logger.info(f"ML model trained for {symbol}. Accuracy: {accuracy:.4f}")
            self.model_trained = True
            self.is_model_trained = True
            
        except Exception as e:
            logger.error(f"Error training ML model for {symbol}: {e}")
    
    def _get_model_prediction(self, features_df: pd.DataFrame) -> Dict:
        """
        Get prediction from the trained ML model
        """
        if not self.model_trained or self.ml_model is None:
            return {'prediction': 0, 'confidence': 0.0}
        
        # Select only the feature columns that were used for training
        latest_features = features_df[self.feature_columns].iloc[-1:].dropna()
        
        if len(latest_features) == 0 or len(latest_features.columns) != len(self.feature_columns):
            return {'prediction': 0, 'confidence': 0.0}
        
        # Get prediction probabilities
        try:
            prediction_proba = self.ml_model.predict_proba(latest_features)[0]
            prediction = self.ml_model.predict(latest_features)[0]
            
            # Confidence is the probability of the predicted class
            confidence = max(prediction_proba)
            
            return {
                'prediction': int(prediction),
                'confidence': float(confidence)
            }
        except Exception as e:
            logger.error(f"Error getting ML prediction: {e}")
            return {'prediction': 0, 'confidence': 0.0}
    
    def save_model(self, filepath: str):
        """
        Save the trained model to disk
        """
        if self.ml_model is not None:
            joblib.dump({
                'model': self.ml_model,
                'feature_columns': self.feature_columns,
                'model_trained': self.model_trained
            }, filepath)
            logger.info(f"Model saved to {filepath}")
    
    def load_model(self, filepath: str):
        """
        Load a trained model from disk
        """
        try:
            model_data = joblib.load(filepath)
            self.ml_model = model_data['model']
            self.feature_columns = model_data['feature_columns']
            self.model_trained = model_data['model_trained']
            self.is_model_trained = True
            logger.info(f"Model loaded from {filepath}")
        except Exception as e:
            logger.error(f"Error loading model from {filepath}: {e}")