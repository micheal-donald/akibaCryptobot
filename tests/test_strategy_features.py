import pytest
import pandas as pd
import numpy as np
import os
import tempfile
import joblib
from unittest.mock import patch
from datetime import datetime, timedelta
from strategies.ensemble_strategy import EnsembleStrategy
from strategies.freqai.ml_freqai_strategy import MLFreqAIStrategy
from strategies.momentum_strategy import MomentumStrategy


@pytest.fixture
def mock_config():
    return {
        "freqai": {
            "enabled": True,
            "model": "LightGBMClassifier",
            "model_expiry_hours": 48,
            "train_period": 200,
            "ml_params": {"lookahead_period": 5, "training_data_period": 1000},
        },
        "trading": {"timeframes": {"primary": "1h"}},
        "strategy_config": {},
    }


def test_feature_8_model_expiry(mock_config):
    """Models with trained_at are checked for expiry; expired models return HOLD."""
    import sklearn.ensemble

    model = sklearn.ensemble.RandomForestClassifier(n_estimators=2)
    feature_cols = ["a"]

    with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
        tmp_path = f.name

    strategy = MLFreqAIStrategy(mock_config)
    strategy.ml_model = model
    strategy.feature_columns = feature_cols
    strategy.model_trained = True

    # 1. Save and verify trained_at is there
    strategy.save_model(tmp_path)
    data = joblib.load(tmp_path)
    assert "trained_at" in data

    # 2. Fresh load - should not be expired
    s2 = MLFreqAIStrategy(mock_config)
    s2.load_model(tmp_path)
    assert s2.model_expired is False

    # 3. Old model — manually set trained_at 72h ago
    data["trained_at"] = (datetime.now() - timedelta(hours=72)).isoformat()
    joblib.dump(data, tmp_path)

    s3 = MLFreqAIStrategy(mock_config)
    s3.load_model(tmp_path)
    assert s3.model_expired is True

    # 4. Expired model should return HOLD (prediction=0, confidence=0.0)
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
    pred = s3._get_model_prediction(df)
    assert pred["prediction"] == 0
    assert pred["confidence"] == pytest.approx(0.0)

    os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_feature_4_drop_incomplete_candle_momentum():
    """MomentumStrategy.generate_signal uses iloc[-2] (completed candle), not iloc[-1]."""
    strategy = MomentumStrategy(
        {"strategy_config": {"rsi_oversold": 30, "rsi_overbought": 70}}
    )

    # Build a 60-row DataFrame so len > 50 check passes
    n = 60
    df = pd.DataFrame(
        {
            "open": np.linspace(100, 110, n),
            "high": np.linspace(105, 115, n),
            "low": np.linspace(95, 105, n),
            "close": np.linspace(100, 110, n),
            "volume": [1000] * n,
        }
    )

    # Mock calculate_indicators to return a DF where
    # row -2 is neutral, row -1 is extremely overbought
    indicator_df = df.copy()
    indicator_df["rsi"] = 50.0  # neutral everywhere
    indicator_df["macd"] = 0.0
    indicator_df["macd_signal"] = 0.0
    indicator_df["sma_20"] = 105.0
    indicator_df["stoch_k"] = 50.0
    indicator_df["adx"] = 20.0
    indicator_df["atr"] = 1.0

    # Make row -1 extremely overbought (RSI=90) — would trigger SELL if used
    indicator_df.iloc[-1, indicator_df.columns.get_loc("rsi")] = 90.0
    indicator_df.iloc[-1, indicator_df.columns.get_loc("stoch_k")] = 95.0

    with patch.object(strategy, "calculate_indicators", return_value=indicator_df):
        signal = await strategy.generate_signal("BTC/USDT", df, 110.0)

    # Row -2 has RSI=50, ADX=20 (<25 threshold) — no clear signal → should return None
    # If it mistakenly used row -1 (RSI=90), it would generate a SELL signal
    assert signal is None


# -----------------------------------------------------------------------------
# Per-symbol ML model registry regression tests
#
# Before the registry, EnsembleStrategy.load_models called load_model(filepath)
# in a loop which stomped self.ml_model once per file — so only the last-loaded
# classifier was ever used at inference time. The tests below pin down the new
# invariant: each <SYMBOL>_freqai.joblib lands in its own registry slot, and
# inference for a given symbol resolves to that symbol's classifier.
# -----------------------------------------------------------------------------


class _FixedClassifier:
    """Deterministic stand-in for a fitted sklearn/LightGBM classifier.

    Used by the registry tests to prove that inference for a given symbol
    routes to that symbol's model — the prediction value is effectively a
    fingerprint of which classifier got called.
    """

    def __init__(self, prediction: int, confidence: float):
        self._prediction = int(prediction)
        self._confidence = float(confidence)

    def predict(self, X):
        return np.array([self._prediction] * len(X))

    def predict_proba(self, X):
        if self._prediction == 1:
            row = [1.0 - self._confidence, self._confidence]
        else:
            row = [self._confidence, 1.0 - self._confidence]
        return np.array([row] * len(X))


def _make_model_file(tmp_dir, symbol_filename: str, classifier, feature_columns):
    """Write a classifier to disk in the format load_model expects."""

    filepath = os.path.join(tmp_dir, symbol_filename)
    joblib.dump(
        {
            "model": classifier,
            "feature_columns": feature_columns,
            "model_trained": True,
            "trained_at": datetime.now().isoformat(),
        },
        filepath,
    )
    return filepath


def test_symbol_from_model_filename_parses_known_patterns():
    """The strategy owns its filename convention (OCP — the ensemble does not hardcode it)."""

    parse = MLFreqAIStrategy.symbol_from_model_filename

    assert parse("BTC_USDT_freqai.joblib") == "BTCUSDT"
    assert parse("ETH_USDT_freqai.joblib") == "ETHUSDT"
    assert parse("SOL_USDT_freqai.joblib") == "SOLUSDT"
    # Single-token filenames still parse cleanly.
    assert parse("DOGEUSDT_freqai.joblib") == "DOGEUSDT"
    # Anything not matching this strategy's convention is rejected so the
    # ensemble falls back to the single-slot load path.
    assert parse("random.joblib") is None
    assert parse("_freqai.joblib") is None


def test_load_model_for_symbol_populates_registry(mock_config, tmp_path):
    """``load_model_for_symbol`` stores the model in the registry without stomping other slots."""

    classifier = _FixedClassifier(prediction=1, confidence=0.9)
    filepath = _make_model_file(
        str(tmp_path), "BTC_USDT_freqai.joblib", classifier, ["a", "b"]
    )

    strategy = MLFreqAIStrategy(mock_config)
    strategy.load_model_for_symbol("BTC/USDT", filepath)

    # Joblib pickles classifiers, so object identity is lost across the
    # save/load round-trip. Compare by behavior — the reconstructed
    # classifier must produce the same fingerprint.
    entry = strategy.registry.resolve("BTCUSDT")
    assert entry is not None
    sample = pd.DataFrame({"a": [1.0], "b": [0.1]})
    assert int(entry.model.predict(sample)[0]) == 1
    assert float(max(entry.model.predict_proba(sample)[0])) == pytest.approx(0.9)
    assert entry.feature_columns == ["a", "b"]
    assert entry.is_expired is False
    assert strategy.is_model_trained is True


def test_load_model_preserves_single_slot_behavior(mock_config, tmp_path):
    """Backwards-compat: legacy ``load_model(filepath)`` writes the single slot only."""

    classifier = _FixedClassifier(prediction=0, confidence=0.8)
    filepath = _make_model_file(
        str(tmp_path), "BTC_USDT_freqai.joblib", classifier, ["a"]
    )

    strategy = MLFreqAIStrategy(mock_config)
    strategy.load_model(filepath)

    sample = pd.DataFrame({"a": [1.0]})
    assert int(strategy.ml_model.predict(sample)[0]) == 0
    assert strategy.feature_columns == ["a"]
    assert strategy.model_trained is True
    # Registry stays empty when no symbol is provided.
    assert len(strategy.registry) == 0


def test_ensemble_load_models_keeps_all_symbol_models_distinct(mock_config, tmp_path):
    """Regression for the single-slot bug: 4 models must survive the load loop.

    Before this fix, ensemble.load_models called load_model(filepath) in a
    loop and each call overwrote the single slot — so only the last file
    (typically whichever the filesystem yielded last) was ever effective at
    inference. Now each file lands in its own registry slot and every
    symbol keeps its own classifier.
    """

    btc = _FixedClassifier(prediction=1, confidence=0.95)
    eth = _FixedClassifier(prediction=0, confidence=0.90)
    sol = _FixedClassifier(prediction=1, confidence=0.85)
    bnb = _FixedClassifier(prediction=0, confidence=0.80)

    _make_model_file(str(tmp_path), "BTC_USDT_freqai.joblib", btc, ["a"])
    _make_model_file(str(tmp_path), "ETH_USDT_freqai.joblib", eth, ["a"])
    _make_model_file(str(tmp_path), "SOL_USDT_freqai.joblib", sol, ["a"])
    _make_model_file(str(tmp_path), "BNB_USDT_freqai.joblib", bnb, ["a"])

    ensemble = EnsembleStrategy(mock_config)
    loaded = ensemble.load_models(str(tmp_path))

    assert loaded == 4
    registry = ensemble.freqai.registry
    assert set(registry.keys()) == {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"}

    # Joblib pickles the classifiers on save, so identity is lost on load.
    # Compare by behavior instead — each symbol's entry must produce the
    # prediction value of the classifier that was saved for that symbol.
    sample = pd.DataFrame({"a": [1.0]})
    assert int(registry.resolve("BTCUSDT").model.predict(sample)[0]) == 1
    assert int(registry.resolve("ETHUSDT").model.predict(sample)[0]) == 0
    assert int(registry.resolve("SOLUSDT").model.predict(sample)[0]) == 1
    assert int(registry.resolve("BNBUSDT").model.predict(sample)[0]) == 0


def test_get_prediction_for_symbol_routes_to_correct_registry_entry(mock_config):
    """Each symbol's inference call must resolve to that symbol's classifier."""

    strategy = MLFreqAIStrategy(mock_config)
    strategy.registry.register(
        "BTCUSDT",
        model=_FixedClassifier(prediction=1, confidence=0.95),
        feature_columns=["a", "b"],
        trained_at=None,
        is_expired=False,
    )
    strategy.registry.register(
        "ETHUSDT",
        model=_FixedClassifier(prediction=0, confidence=0.90),
        feature_columns=["a", "b"],
        trained_at=None,
        is_expired=False,
    )
    strategy.is_model_trained = True

    features_df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [0.1, 0.2, 0.3]})

    btc_pred = strategy._get_prediction_for_symbol("BTC/USDT", features_df)
    eth_pred = strategy._get_prediction_for_symbol("ETH/USDT", features_df)

    assert btc_pred["prediction"] == 1
    assert btc_pred["confidence"] == pytest.approx(0.95)
    assert eth_pred["prediction"] == 0
    assert eth_pred["confidence"] == pytest.approx(0.90)


def test_get_prediction_for_symbol_short_circuits_on_expired_registry_entry(
    mock_config,
):
    """Expired per-symbol models must return HOLD, same as the single-slot path."""

    strategy = MLFreqAIStrategy(mock_config)
    strategy.registry.register(
        "BTCUSDT",
        model=_FixedClassifier(prediction=1, confidence=0.99),
        feature_columns=["a"],
        trained_at=None,
        is_expired=True,
    )
    strategy.is_model_trained = True

    features_df = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
    pred = strategy._get_prediction_for_symbol("BTCUSDT", features_df)

    assert pred["prediction"] == 0
    assert pred["confidence"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_generate_signal_returns_none_when_registry_has_no_symbol_entry(
    mock_config,
):
    """A symbol with no registry entry and no single slot must not produce a signal.

    The ensemble will fall back to momentum in that case. The old parent
    implementation would run feature engineering and then return HOLD via
    ``confidence=0``; the override reaches the same outcome earlier and
    more cheaply.
    """

    strategy = MLFreqAIStrategy(mock_config)
    assert strategy.ml_model is None
    assert len(strategy.registry) == 0

    dates = pd.date_range("2026-04-16", periods=100, freq="h")
    ohlcv = pd.DataFrame(
        {
            "date": dates,
            "open": np.linspace(100, 110, 100),
            "high": np.linspace(105, 115, 100),
            "low": np.linspace(95, 105, 100),
            "close": np.linspace(100, 110, 100),
            "volume": [1000] * 100,
        }
    )

    signal = await strategy.generate_signal("BTC/USDT", ohlcv, 105.0)
    assert signal is None


def test_symbol_model_registry_normalizes_symbol_shapes():
    """Registry key normalization collapses ``BTC/USDT`` / ``BTC_USDT`` / ``BTCUSDT``."""

    from strategies.freqai.symbol_model_registry import SymbolModelRegistry

    registry = SymbolModelRegistry()
    registry.register(
        "BTC/USDT",
        model=_FixedClassifier(prediction=1, confidence=0.5),
        feature_columns=["a"],
        trained_at=None,
        is_expired=False,
    )

    # All three spellings resolve to the same slot.
    assert registry.has("BTC/USDT")
    assert registry.has("BTC_USDT")
    assert registry.has("BTCUSDT")
    assert registry.resolve("BTC_USDT") is registry.resolve("BTCUSDT")
    assert registry.keys() == ["BTCUSDT"]
