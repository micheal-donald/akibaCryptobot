import pandas as pd
import numpy as np
from typing import Dict, Optional
from datetime import datetime
import logging
import sys
import os

# Add parent directory to path for imports
parent_dir = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from strategies.freqai.base_freqai_strategy import (
    BaseFreqAIStrategy,
    TradingSignal,
    SignalType,
)
from indicators.technical_indicators import TechnicalIndicators

logger = logging.getLogger(__name__)


class ExampleFreqAIStrategy(BaseFreqAIStrategy):
    """
    Example FreqAI strategy demonstrating integration with akibaCryptobot

    This strategy uses machine learning to predict price movements based on
    technical indicators and market features.
    """

    def __init__(self, config: Dict):
        super().__init__(config)

        # Strategy-specific parameters
        self.rsi_period = config.get("rsi_period", 14)
        self.macd_fast = config.get("macd_fast", 12)
        self.macd_slow = config.get("macd_slow", 26)
        self.macd_signal = config.get("macd_signal", 9)
        self.bb_period = config.get("bb_period", 20)
        self.bb_std = config.get("bb_std", 2)

        logger.info(
            f"ExampleFreqAIStrategy initialized with RSI({self.rsi_period}), "
            f"MACD({self.macd_fast},{self.macd_slow},{self.macd_signal})"
        )

    def feature_engineering_standard(
        self, dataframe: pd.DataFrame, **kwargs
    ) -> pd.DataFrame:
        """
        Standard feature engineering that applies to a single timeframe
        """
        # Add custom features for the model
        dataframe["%-rsi"] = TechnicalIndicators.rsi(
            dataframe["close"], self.rsi_period
        )
        dataframe["%-macd"] = TechnicalIndicators.macd(
            dataframe["close"], self.macd_fast, self.macd_slow, self.macd_signal
        )["macd"]
        dataframe["%-macd_signal"] = TechnicalIndicators.macd(
            dataframe["close"], self.macd_fast, self.macd_slow, self.macd_signal
        )["macd_signal"]

        # Bollinger Bands features
        bb_data = TechnicalIndicators.bollinger_bands(
            dataframe["close"], self.bb_period, self.bb_std
        )
        dataframe["%-bb_width"] = bb_data["bb_width"]
        dataframe["%-bb_percent"] = bb_data["bb_percent"]

        # Price change features
        dataframe["%-price_change_1"] = dataframe["close"].pct_change(1)
        dataframe["%-price_change_5"] = dataframe["close"].pct_change(5)
        dataframe["%-price_change_10"] = dataframe["close"].pct_change(10)

        # Volume features
        dataframe["%-volume_change"] = dataframe["volume"].pct_change(1)
        dataframe["%-volume_sma"] = (
            dataframe["volume"] / dataframe["volume"].rolling(20).mean()
        )

        # Volatility features
        dataframe["%-volatility"] = dataframe["close"].pct_change().rolling(10).std()

        return dataframe

    def set_freqai_targets(self, dataframe: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """
        Set targets for FreqAI model training
        Predict if price will go up/down in next N candles
        """
        # Predict if price will be higher in 5 candles (1 for up, 0 for down)
        lookahead = 5
        dataframe["&-s_close"] = (
            dataframe["close"].shift(-lookahead) > dataframe["close"]
        ).astype(int)

        return dataframe

    def calculate_indicators(self, ohlcv_data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate technical indicators for the strategy
        """
        df = ohlcv_data.copy()

        # Basic indicators
        df["rsi"] = TechnicalIndicators.rsi(df["close"], self.rsi_period)
        macd_data = TechnicalIndicators.macd(
            df["close"], self.macd_fast, self.macd_slow, self.macd_signal
        )
        df["macd"] = macd_data["macd"]
        df["macd_signal"] = macd_data["macd_signal"]

        # Bollinger Bands
        bb_data = TechnicalIndicators.bollinger_bands(
            df["close"], self.bb_period, self.bb_std
        )
        df["bb_upper"] = bb_data["bb_upper"]
        df["bb_lower"] = bb_data["bb_lower"]

        return df

    async def generate_signal(
        self, symbol: str, ohlcv_data: pd.DataFrame, current_price: float
    ) -> Optional[TradingSignal]:
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
                logger.warning(
                    f"Insufficient data for {symbol}: {len(ohlcv_data)} bars"
                )
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

    def _get_model_prediction(self, features_df: pd.DataFrame) -> Dict:
        """
        Get prediction from the trained model
        In this example, we're using a simple rule-based approach
        In a real implementation, this would call the actual FreqAI model
        """
        latest_features = features_df.iloc[-1:].dropna()

        if len(latest_features) == 0:
            return {"prediction": 0, "confidence": 0.0}

        # Simple rule-based prediction for demonstration
        rsi = latest_features["rsi"].iloc[0]
        macd = latest_features["macd"].iloc[0]
        macd_signal = latest_features["macd_signal"].iloc[0]

        # Buy signal conditions
        if rsi < 30 and macd > macd_signal:
            prediction = 1  # Buy
            confidence = min(1.0, (30 - rsi) / 30 + 0.5)
        # Sell signal conditions
        elif rsi > 70 and macd < macd_signal:
            prediction = 2  # Sell
            confidence = min(1.0, (rsi - 70) / 30 + 0.5)
        else:
            prediction = 0  # Hold
            confidence = 0.3

        return {"prediction": prediction, "confidence": confidence}
