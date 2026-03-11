import pandas as pd
from typing import Dict
from datetime import datetime
import logging
import lightgbm as lgb
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
import joblib
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
)
from indicators.technical_indicators import TechnicalIndicators

logger = logging.getLogger(f"akiba_crypto_bot.{__name__}")


class MLFreqAIStrategy(BaseFreqAIStrategy):
    """
    Machine Learning FreqAI strategy using LightGBM for fast, accurate predictions.

    Supports multiple ML models:
    - LightGBMClassifier (default, 10x faster)
    - RandomForest
    - XGBoost (if installed)
    """

    def __init__(self, config: Dict):
        super().__init__(config)

        # ML-specific parameters
        self.ml_config = self.freqai_config.get("ml_params", {})
        self.lookahead_period = self.ml_config.get("lookahead_period", 5)
        self.training_data_period = self.ml_config.get("training_data_period", 1000)
        self.feature_columns = []

        # Initialize ML model
        self.ml_model = None
        self.model_trained = False
        self.model_trained_at = None
        self.model_expired = False

        logger.info(
            f"MLFreqAIStrategy initialized with lookahead={self.lookahead_period}"
        )

    def feature_engineering_standard(
        self, dataframe: pd.DataFrame, **kwargs
    ) -> pd.DataFrame:
        """
        Standard feature engineering for ML model
        """
        # Technical indicators as features
        dataframe["%-rsi"] = TechnicalIndicators.rsi(dataframe["close"], 14)
        dataframe["%-macd"] = TechnicalIndicators.macd(dataframe["close"])["macd"]
        dataframe["%-macd_signal"] = TechnicalIndicators.macd(dataframe["close"])[
            "macd_signal"
        ]

        # Bollinger Bands
        bb_data = TechnicalIndicators.bollinger_bands(dataframe["close"])
        dataframe["%-bb_width"] = bb_data["bb_width"]
        dataframe["%-bb_percent"] = bb_data["bb_percent"]

        # Price-based features
        dataframe["%-price_change_1"] = dataframe["close"].pct_change(1)
        dataframe["%-price_change_3"] = dataframe["close"].pct_change(3)
        dataframe["%-price_change_5"] = dataframe["close"].pct_change(5)
        dataframe["%-price_change_10"] = dataframe["close"].pct_change(10)

        # Moving averages
        dataframe["%-sma_10"] = TechnicalIndicators.sma(dataframe["close"], 10)
        dataframe["%-sma_20"] = TechnicalIndicators.sma(dataframe["close"], 20)
        dataframe["%-sma_50"] = TechnicalIndicators.sma(dataframe["close"], 50)

        # Price position relative to moving averages
        dataframe["%-close_vs_sma_10"] = (
            dataframe["close"] / dataframe["%-sma_10"]
        ) - 1
        dataframe["%-close_vs_sma_20"] = (
            dataframe["close"] / dataframe["%-sma_20"]
        ) - 1

        # Volume features
        dataframe["%-volume_change"] = dataframe["volume"].pct_change(1)
        dataframe["%-volume_sma_ratio"] = (
            dataframe["volume"] / dataframe["volume"].rolling(20).mean()
        )

        # Volatility
        dataframe["%-volatility_10"] = dataframe["close"].pct_change().rolling(10).std()
        dataframe["%-volatility_20"] = dataframe["close"].pct_change().rolling(20).std()

        # Time-based features
        dataframe["%-hour"] = pd.to_datetime(dataframe["date"]).dt.hour
        dataframe["%-day_of_week"] = pd.to_datetime(dataframe["date"]).dt.dayofweek

        return dataframe

    def set_freqai_targets(self, dataframe: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """
        Set targets for ML model training
        """
        # Classification target: 1 if price goes up in lookahead_period, 0 otherwise
        dataframe["&-price_up"] = (
            dataframe["close"].shift(-self.lookahead_period) > dataframe["close"]
        ).astype(int)

        # Regression target: percentage change in price
        dataframe["&-price_change"] = (
            dataframe["close"].shift(-self.lookahead_period) - dataframe["close"]
        ) / dataframe["close"]

        return dataframe

    def calculate_indicators(self, ohlcv_data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate technical indicators for the strategy
        """
        df = ohlcv_data.copy()

        # Basic indicators
        df["rsi"] = TechnicalIndicators.rsi(df["close"], 14)
        macd_data = TechnicalIndicators.macd(df["close"])
        df["macd"] = macd_data["macd"]
        df["macd_signal"] = macd_data["macd_signal"]

        # Moving averages
        df["sma_10"] = TechnicalIndicators.sma(df["close"], 10)
        df["sma_20"] = TechnicalIndicators.sma(df["close"], 20)

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
            feature_cols = [
                col for col in training_data.columns if col.startswith("%-")
            ]
            target_col = "&-price_up"

            # Store feature column names for prediction
            self.feature_columns = feature_cols

            # Remove rows with NaN values
            clean_data = training_data.dropna()

            if len(clean_data) < 100:
                logger.warning(
                    f"Insufficient clean data for training: {len(clean_data)} rows"
                )
                return

            X = clean_data[feature_cols]
            y = clean_data[target_col]

            # Split data for training and validation
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42, stratify=y
            )

            # Initialize and train the model based on config
            model_type = self.freqai_model.lower() if self.freqai_model else "lightgbm"

            if model_type in ["lightgbm", "lightgbmclassifier", "lgbm"]:
                # LightGBM - 10x faster training, better accuracy
                self.ml_model = lgb.LGBMClassifier(
                    n_estimators=200,
                    learning_rate=0.05,
                    num_leaves=31,
                    max_depth=10,
                    random_state=42,
                    verbose=-1,  # Suppress output
                    n_jobs=-1,
                    importance_type="gain",
                )
                logger.info("Using LightGBM classifier (10x faster than RandomForest)")
            elif model_type in ["randomforest", "rf"]:
                self.ml_model = RandomForestClassifier(
                    n_estimators=100, max_depth=10, random_state=42, n_jobs=-1
                )
                logger.info("Using RandomForest classifier")
            else:
                # Default to LightGBM
                self.ml_model = lgb.LGBMClassifier(
                    n_estimators=200,
                    learning_rate=0.05,
                    num_leaves=31,
                    random_state=42,
                    verbose=-1,
                )
                logger.info(
                    f"Unknown model type '{model_type}', defaulting to LightGBM"
                )

            # Train the model
            self.ml_model.fit(X_train, y_train)

            # Evaluate the model with multiple metrics
            y_pred = self.ml_model.predict(X_test)
            accuracy = accuracy_score(y_test, y_pred)
            precision = precision_score(y_test, y_pred, zero_division=0)
            recall = recall_score(y_test, y_pred, zero_division=0)
            f1 = f1_score(y_test, y_pred, zero_division=0)

            logger.info(f"ML model trained for {symbol}:")
            logger.info(f"  Accuracy: {accuracy:.4f}")
            logger.info(f"  Precision: {precision:.4f}")
            logger.info(f"  Recall: {recall:.4f}")
            logger.info(f"  F1 Score: {f1:.4f}")

            # Log feature importance for LightGBM
            if hasattr(self.ml_model, "feature_importances_"):
                importances = dict(
                    zip(self.feature_columns, self.ml_model.feature_importances_)
                )
                top_features = sorted(
                    importances.items(), key=lambda x: x[1], reverse=True
                )[:5]
                logger.info(f"  Top features: {top_features}")

            self.model_trained = True
            self.is_model_trained = True

        except Exception as e:
            logger.error(f"Error training ML model for {symbol}: {e}")

    def _get_model_prediction(self, features_df: pd.DataFrame) -> Dict:
        """
        Get prediction from the trained ML model
        """
        if not self.model_trained or self.ml_model is None:
            return {"prediction": 0, "confidence": 0.0}

        # Option A: skip expired models entirely, fall back to HOLD so ensemble
        # uses momentum-only signal instead of potentially stale ML output.
        if getattr(self, "model_expired", False):
            logger.warning("Skipping ML prediction - model is expired")
            return {"prediction": 0, "confidence": 0.0}
        # Select only the feature columns that were used for training
        # We use iloc[-2:-1] to predict using the last *completed* candle, not the live partial candle
        latest_features = features_df[self.feature_columns].iloc[-2:-1].dropna()

        if len(latest_features) == 0 or len(latest_features.columns) != len(
            self.feature_columns
        ):
            return {"prediction": 0, "confidence": 0.0}

        # Get prediction probabilities
        try:
            prediction_proba = self.ml_model.predict_proba(latest_features)[0]
            prediction = self.ml_model.predict(latest_features)[0]

            # Confidence is the probability of the predicted class
            confidence = max(prediction_proba)

            return {"prediction": int(prediction), "confidence": float(confidence)}
        except Exception as e:
            logger.error(f"Error getting ML prediction: {e}")
            return {"prediction": 0, "confidence": 0.0}

    def save_model(self, filepath: str):
        """
        Save the trained model to disk, including a timestamp for expiry checks.
        """
        if self.ml_model is not None:
            joblib.dump(
                {
                    "model": self.ml_model,
                    "feature_columns": self.feature_columns,
                    "model_trained": self.model_trained,
                    "trained_at": datetime.now().isoformat(),
                },
                filepath,
            )
            logger.info(f"Model saved to {filepath}")

    def load_model(self, filepath: str):
        """
        Load a trained model from disk and check whether it has expired.
        """
        try:
            model_data = joblib.load(filepath)
            self.ml_model = model_data["model"]
            self.feature_columns = model_data["feature_columns"]
            self.model_trained = model_data["model_trained"]
            self.is_model_trained = True

            # Check model age against configured expiry window
            trained_at_str = model_data.get("trained_at")
            if trained_at_str:
                self.model_trained_at = datetime.fromisoformat(trained_at_str)
                max_age_hours = self.freqai_config.get("model_expiry_hours", 48)
                age_hours = (
                    datetime.now() - self.model_trained_at
                ).total_seconds() / 3600
                if age_hours > max_age_hours:
                    logger.warning(
                        f"Model from {filepath} is {age_hours:.1f}h old "
                        f"(max: {max_age_hours}h) - predictions may be stale"
                    )
                    self.model_expired = True
                else:
                    self.model_expired = False
                    logger.info(
                        f"Model age: {age_hours:.1f}h (within {max_age_hours}h limit)"
                    )
            else:
                # Backwards compat: old models without a timestamp are allowed
                logger.warning(f"Model {filepath} has no trained_at timestamp")
                self.model_expired = False

            logger.info(f"Model loaded from {filepath}")
        except Exception as e:
            logger.error(f"Error loading model from {filepath}: {e}")
