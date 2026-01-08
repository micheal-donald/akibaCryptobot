import pytest
import pandas as pd
import numpy as np
from unittest.mock import AsyncMock, Mock, patch
import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from strategies.freqai.example_freqai_strategy import ExampleFreqAIStrategy
from strategies.freqai.ml_freqai_strategy import MLFreqAIStrategy
from strategies.base_strategy import TradingSignal, SignalType


@pytest.fixture
def sample_ohlcv_data():
    """Create sample OHLCV data for testing"""
    dates = pd.date_range("2023-01-01", periods=100, freq="h")
    return pd.DataFrame(
        {
            "date": dates,
            "open": np.random.uniform(40000, 50000, 100),
            "high": np.random.uniform(41000, 51000, 100),
            "low": np.random.uniform(39000, 49000, 100),
            "close": np.random.uniform(40000, 50000, 100),
            "volume": np.random.uniform(1000, 2000, 100),
        }
    )


@pytest.fixture
def freqai_config():
    """Sample FreqAI configuration"""
    return {
        "freqai": {
            "enabled": True,
            "model": "LightGBMClassifier",
            "identifier": "test_model",
            "train_period": 100,
        },
        "min_signal_strength": 0.6,
        "stop_loss_percent": 0.02,
        "take_profit_percent": 0.04,
    }


def test_example_freqai_strategy_initialization(freqai_config):
    """Test ExampleFreqAIStrategy initialization"""
    strategy = ExampleFreqAIStrategy(freqai_config)

    assert strategy.freqai_enabled == True
    assert strategy.freqai_model == "LightGBMClassifier"
    assert hasattr(strategy, "feature_engineering_standard")
    assert hasattr(strategy, "set_freqai_targets")


def test_ml_freqai_strategy_initialization(freqai_config):
    """Test MLFreqAIStrategy initialization"""
    strategy = MLFreqAIStrategy(freqai_config)

    assert strategy.freqai_enabled == True
    assert strategy.lookahead_period == 5
    assert strategy.ml_model is None
    assert strategy.model_trained == False


@pytest.mark.asyncio
async def test_example_freqai_generate_signal(freqai_config, sample_ohlcv_data):
    """Test signal generation for example strategy"""
    strategy = ExampleFreqAIStrategy(freqai_config)
    strategy.is_model_trained = True

    signal = await strategy.generate_signal("BTC/USDT", sample_ohlcv_data, 45000.0)

    # Signal may be None or a TradingSignal depending on random values
    assert signal is None or isinstance(signal, TradingSignal)


def test_feature_engineering_standard(sample_ohlcv_data):
    """Test feature engineering"""
    config = {
        "freqai": {
            "enabled": True,
            "model": "LightGBMClassifier",
            "identifier": "test_model",
        }
    }

    strategy = ExampleFreqAIStrategy(config)
    result_df = strategy.feature_engineering_standard(sample_ohlcv_data.copy())

    # Check that new feature columns were added
    assert "%-rsi" in result_df.columns
    assert "%-macd" in result_df.columns
    assert "%-price_change_1" in result_df.columns
    assert "%-volume_change" in result_df.columns


def test_set_freqai_targets(sample_ohlcv_data):
    """Test setting targets for training"""
    config = {
        "freqai": {
            "enabled": True,
            "model": "LightGBMClassifier",
            "identifier": "test_model",
        }
    }

    strategy = ExampleFreqAIStrategy(config)
    result_df = strategy.set_freqai_targets(sample_ohlcv_data.copy())

    # Check that target column was added
    assert "&-s_close" in result_df.columns


if __name__ == "__main__":
    pytest.main([__file__])
