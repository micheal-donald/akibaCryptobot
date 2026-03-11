import pytest
import pandas as pd
import numpy as np
import os
import tempfile
import joblib
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from strategies.freqai.ml_freqai_strategy import MLFreqAIStrategy
from strategies.momentum_strategy import MomentumStrategy
from strategies.base_strategy import SignalType


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
