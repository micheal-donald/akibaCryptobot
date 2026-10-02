import pandas as pd
from typing import Any, Dict, List, Optional, Tuple
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
from strategies.freqai.symbol_model_registry import SymbolModelRegistry
from strategies.base_strategy import TradingSignal, SignalType
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
        self.training_n_jobs = int(self.ml_config.get("n_jobs", -1))
        self.feature_columns = []

        # Legacy single-slot model — kept so ``train_model`` / ``save_model``
        # and tests that poke ``self.ml_model`` directly continue to work
        # unchanged. Runtime inference in the ensemble routes through the
        # registry below; the single slot is only consulted as a fallback
        # when the registry is empty (e.g. during training workflows that
        # haven't persisted yet).
        self.ml_model = None
        self.model_trained = False
        self.model_trained_at = None
        self.model_expired = False

        # Per-symbol model registry. Primary source of truth for models
        # loaded from disk. EnsembleStrategy.load_models parses the symbol
        # from each filename and registers each model under its own key,
        # instead of the prior behavior where load_model(filepath) was
        # called in a loop and each call overwrote the single slot — which
        # left only the last-loaded classifier effective at inference time.
        self.registry = SymbolModelRegistry()

        logger.info(
            f"MLFreqAIStrategy initialized with lookahead={self.lookahead_period}"
        )

    # --- Filename ↔ symbol convention -----------------------------------

    _MODEL_FILENAME_SUFFIX = "_freqai.joblib"

    @classmethod
    def symbol_from_model_filename(cls, filename: str) -> Optional[str]:
        """Parse ``BTC_USDT_freqai.joblib`` → ``BTCUSDT``.

        Lives on the strategy (not on the ensemble) so each ML strategy
        owns its own filename convention. ``EnsembleStrategy.load_models``
        asks the strategy how to parse its own files — that keeps the
        ensemble open/closed with respect to new strategies.

        Returns ``None`` when the filename doesn't match this strategy's
        convention; callers should treat that as "fall back to the
        single-slot load path".
        """

        if not filename.endswith(cls._MODEL_FILENAME_SUFFIX):
            return None
        stem = filename[: -len(cls._MODEL_FILENAME_SUFFIX)]
        if not stem:
            return None
        return SymbolModelRegistry.normalize(stem)

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
                    n_jobs=self.training_n_jobs,
                    importance_type="gain",
                )
                logger.info("Using LightGBM classifier (10x faster than RandomForest)")
            elif model_type in ["randomforest", "rf"]:
                self.ml_model = RandomForestClassifier(
                    n_estimators=100,
                    max_depth=10,
                    random_state=42,
                    n_jobs=self.training_n_jobs,
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
                    n_jobs=self.training_n_jobs,
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

    async def generate_signal(
        self, symbol: str, ohlcv_data: pd.DataFrame, current_price: float
    ) -> Optional[TradingSignal]:
        """Symbol-aware override that routes prediction through the registry.

        The parent's ``generate_signal`` assumes a single ``self.ml_model``.
        After the per-symbol registry change, that assumption is wrong when
        EnsembleStrategy.load_models has populated multiple models. This
        override threads the symbol through to ``_get_prediction_for_symbol``
        so each symbol uses its own classifier.
        """
        try:
            if not self.freqai_enabled:
                logger.warning("FreqAI is not enabled for this strategy")
                return None

            if not self._has_model_for(symbol):
                logger.debug(f"No ML model available for {symbol}")
                return None

            if len(ohlcv_data) < self.lookback_period:
                logger.warning(
                    f"Insufficient data for {symbol}: {len(ohlcv_data)} bars"
                )
                return None

            features_df = self._generate_features(ohlcv_data)
            prediction = self._get_prediction_for_symbol(symbol, features_df)
            logger.debug(f"FreqAI prediction for {symbol}: {prediction}")

            signal = self._prediction_to_signal(prediction, current_price)
            if signal and self.validate_signal(signal):
                self.log_signal(signal, symbol)
                return signal
            return None
        except Exception as e:
            logger.error(f"Error generating FreqAI signal for {symbol}: {e}")
            return None

    def _has_model_for(self, symbol: str) -> bool:
        """True if the registry or the single slot can serve a prediction."""

        if self.registry.has(symbol):
            return True
        return self.ml_model is not None and self.model_trained

    def is_available_for_symbol(self, symbol: str) -> bool:
        """Whether this component can vote using a usable model for the pair."""
        model, feature_columns, is_expired = self._resolve_model(symbol)
        return bool(
            self.freqai_enabled
            and self._has_model_for(symbol)
            and model is not None
            and feature_columns
            and not is_expired
        )

    def _resolve_model(self, symbol: str) -> Tuple[Any, List[str], bool]:
        """Select ``(model, feature_cols, is_expired)`` for ``symbol``.

        Preference order:
          1. Registry entry (populated by ``load_model_for_symbol``).
          2. Single-slot model (``train_model`` / direct attribute assignment
             / legacy ``load_model``).
        """

        entry = self.registry.resolve(symbol)
        if entry is not None:
            return (
                entry.model,
                entry.feature_columns or self.feature_columns,
                entry.is_expired,
            )
        return self.ml_model, self.feature_columns, self.model_expired

    def _get_prediction_for_symbol(
        self, symbol: str, features_df: pd.DataFrame
    ) -> Dict:
        """Registry-aware prediction. Falls back to the single slot if needed."""

        model, feature_cols, is_expired = self._resolve_model(symbol)

        if model is None or not feature_cols:
            return {"prediction": 0, "confidence": 0.0}

        # Option A: skip expired models entirely — the ensemble will fall
        # back to momentum-only rather than trade on stale ML output.
        if is_expired:
            logger.warning(f"Skipping ML prediction for {symbol} - model is expired")
            return {"prediction": 0, "confidence": 0.0}

        # iloc[-2:-1] uses the last *completed* candle, not the live partial one.
        latest_features = features_df[feature_cols].iloc[-2:-1].dropna()
        if len(latest_features) == 0 or len(latest_features.columns) != len(
            feature_cols
        ):
            return {"prediction": 0, "confidence": 0.0}

        try:
            prediction_proba = model.predict_proba(latest_features)[0]
            prediction = model.predict(latest_features)[0]
            confidence = max(prediction_proba)
            return {"prediction": int(prediction), "confidence": float(confidence)}
        except Exception as e:
            logger.error(f"Error getting ML prediction for {symbol}: {e}")
            return {"prediction": 0, "confidence": 0.0}

    def _get_model_prediction(self, features_df: pd.DataFrame) -> Dict:
        """Legacy single-slot prediction path.

        Kept so ``BaseFreqAIStrategy.generate_signal`` (and tests that call
        ``_get_model_prediction`` directly) keep working when an external
        caller has not gone through the symbol-aware override. Prefer
        ``_get_prediction_for_symbol`` in new code.
        """
        if not self.model_trained or self.ml_model is None:
            return {"prediction": 0, "confidence": 0.0}

        if getattr(self, "model_expired", False):
            logger.warning("Skipping ML prediction - model is expired")
            return {"prediction": 0, "confidence": 0.0}

        latest_features = features_df[self.feature_columns].iloc[-2:-1].dropna()

        if len(latest_features) == 0 or len(latest_features.columns) != len(
            self.feature_columns
        ):
            return {"prediction": 0, "confidence": 0.0}

        try:
            prediction_proba = self.ml_model.predict_proba(latest_features)[0]
            prediction = self.ml_model.predict(latest_features)[0]
            confidence = max(prediction_proba)
            return {"prediction": int(prediction), "confidence": float(confidence)}
        except Exception as e:
            logger.error(f"Error getting ML prediction: {e}")
            return {"prediction": 0, "confidence": 0.0}

    def _prediction_to_signal(
        self, prediction: Dict, current_price: float
    ) -> Optional[TradingSignal]:
        """
        Convert binary classifier prediction to trading signal.

        The model is trained with target &-price_up:
          1 = price goes up   → BUY
          0 = price goes down → SELL
        """
        pred_value = prediction.get("prediction", 0)
        confidence = prediction.get("confidence", 0.0)

        if confidence < self.min_signal_strength:
            return None

        if pred_value == 1:  # Price going up
            signal_type = SignalType.BUY
        else:  # pred_value == 0, price going down
            signal_type = SignalType.SELL

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
                "strategy": "freqai_ml",
                "confidence": confidence,
                "model_prediction": pred_value,
            },
        )

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

    def load_model(self, filepath: str) -> None:
        """Load a trained model into the legacy single slot.

        This is the backwards-compatible path for callers that don't know
        about the per-symbol registry — direct attribute-set tests,
        ``train_model`` workflows, and any tool that expects
        ``self.ml_model`` / ``self.feature_columns`` to be populated after
        the call.

        New code that wants per-symbol routing (ensembles with multiple
        pairs) should call :meth:`load_model_for_symbol` instead.
        """

        payload = self._read_model_payload(filepath)
        if payload is None:
            return
        model, feature_columns, model_trained, trained_at, is_expired = payload

        self.ml_model = model
        self.feature_columns = feature_columns
        self.model_trained = model_trained
        self.model_trained_at = trained_at
        self.model_expired = is_expired
        self.is_model_trained = True

        logger.info(f"Model loaded from {filepath}")

    def load_model_for_symbol(self, symbol: str, filepath: str) -> None:
        """Load a trained model into the per-symbol registry.

        ``EnsembleStrategy.load_models`` prefers this method when available
        so each ``<SYMBOL>_freqai.joblib`` lands in its own slot instead
        of stomping a single shared one.

        For backwards compatibility, the first registered entry is mirrored
        into the single-slot fields so external inspectors reading
        ``self.ml_model`` / ``self.model_trained`` still see a populated
        strategy. Subsequent registrations do not overwrite the mirror —
        the registry is the authoritative per-symbol source.
        """

        payload = self._read_model_payload(filepath)
        if payload is None:
            return
        model, feature_columns, model_trained, trained_at, is_expired = payload

        normalized = self.registry.register(
            symbol,
            model=model,
            feature_columns=feature_columns,
            trained_at=trained_at,
            is_expired=is_expired,
        )

        # Keep a non-empty feature_columns fallback for legacy readers.
        if not self.feature_columns:
            self.feature_columns = list(feature_columns)

        # Mirror the first-registered model into the single slot so legacy
        # paths (``_get_model_prediction``, ``save_model``, ``model_trained``
        # introspection) keep working. Don't clobber an already-populated
        # slot — that would reintroduce the very stomping we're fixing.
        if self.ml_model is None:
            self.ml_model = model
            self.model_trained = model_trained
            self.model_trained_at = trained_at
            self.model_expired = is_expired

        self.is_model_trained = True
        logger.info(f"Model loaded from {filepath} → {normalized}")

    def _read_model_payload(
        self, filepath: str
    ) -> Optional[Tuple[Any, List[str], bool, Optional[datetime], bool]]:
        """Load + validate a joblib model file. Returns ``None`` on error."""

        try:
            model_data = joblib.load(filepath)
        except Exception as e:
            logger.error(f"Error loading model from {filepath}: {e}")
            return None

        try:
            model = model_data["model"]
            feature_columns = list(model_data["feature_columns"])
            model_trained = bool(model_data["model_trained"])
        except (KeyError, TypeError) as e:
            logger.error(f"Malformed model payload at {filepath}: {e}")
            return None

        trained_at, is_expired = self._resolve_model_age(
            filepath, model_data.get("trained_at")
        )
        return model, feature_columns, model_trained, trained_at, is_expired

    def _resolve_model_age(
        self, filepath: str, trained_at_str: Optional[str]
    ) -> Tuple[Optional[datetime], bool]:
        """Parse ``trained_at`` and decide whether the model is stale.

        Factored out of the load path so single-slot and per-symbol load
        use identical expiry logic.
        """

        if not trained_at_str:
            logger.warning(f"Model {filepath} has no trained_at timestamp")
            return None, False
        trained_at = datetime.fromisoformat(trained_at_str)
        max_age_hours = self.freqai_config.get("model_expiry_hours", 48)
        age_hours = (datetime.now() - trained_at).total_seconds() / 3600
        if age_hours > max_age_hours:
            logger.warning(
                f"Model from {filepath} is {age_hours:.1f}h old "
                f"(max: {max_age_hours}h) - predictions may be stale"
            )
            return trained_at, True
        logger.info(f"Model age: {age_hours:.1f}h (within {max_age_hours}h limit)")
        return trained_at, False
