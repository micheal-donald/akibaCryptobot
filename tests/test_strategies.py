import pytest
import pandas as pd
import numpy as np
import sys
import os

# Add src to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from strategies.base_strategy import TradingSignal, SignalType
from strategies.momentum_strategy import MomentumStrategy
from risk_management import RiskManager
from indicators.technical_indicators import (
    TechnicalIndicators,
    calculate_all_indicators,
)


@pytest.fixture
def sample_ohlcv_data():
    """Create sample OHLCV data for testing"""
    dates = pd.date_range("2023-01-01", periods=100, freq="1H")
    data = pd.DataFrame(
        {
            "open": np.random.uniform(40000, 50000, 100),
            "high": np.random.uniform(41000, 51000, 100),
            "low": np.random.uniform(39000, 49000, 100),
            "close": np.random.uniform(40000, 50000, 100),
            "volume": np.random.uniform(1000, 10000, 100),
        },
        index=dates,
    )
    return data


@pytest.fixture
def sample_config():
    """Create sample configuration for testing"""
    return {
        "min_signal_strength": 0.6,
        "stop_loss_percent": 0.02,
        "take_profit_percent": 0.04,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "adx_threshold": 25,
    }


class TestBaseStrategy:
    """Test cases for BaseStrategy class"""

    def test_base_strategy_initialization(self, sample_config):
        """Test BaseStrategy initialization via concrete subclass"""
        strategy = MomentumStrategy(sample_config)
        assert strategy.min_signal_strength == 0.6
        assert strategy.stop_loss_percent == 0.02
        assert strategy.take_profit_percent == 0.04

    def test_signal_validation(self, sample_config):
        """Test signal validation"""
        strategy = MomentumStrategy(sample_config)

        # Valid signal
        valid_signal = TradingSignal(
            signal_type=SignalType.BUY,
            strength=0.7,
            price=45000,
            timestamp=pd.Timestamp.now(),
        )
        assert strategy.validate_signal(valid_signal)

        # Invalid signal (low strength)
        invalid_signal = TradingSignal(
            signal_type=SignalType.BUY,
            strength=0.5,
            price=45000,
            timestamp=pd.Timestamp.now(),
        )
        assert not strategy.validate_signal(invalid_signal)


class TestMomentumStrategy:
    """Test cases for MomentumStrategy class"""

    def test_momentum_strategy_initialization(self, sample_config):
        """Test MomentumStrategy initialization"""
        strategy = MomentumStrategy(sample_config)
        assert strategy.rsi_oversold == 30
        assert strategy.rsi_overbought == 70
        assert strategy.adx_threshold == 25

    @pytest.mark.asyncio
    async def test_generate_signal_no_data(self, sample_config):
        """Test signal generation with insufficient data"""
        strategy = MomentumStrategy(sample_config)
        result = await strategy.generate_signal("BTC/USDT", pd.DataFrame(), 45000)
        assert result is None

    @pytest.mark.asyncio
    async def test_should_exit_position(self, sample_config):
        """Test exit position logic"""
        strategy = MomentumStrategy(sample_config)

        # Test stop loss exit
        current_position = {"side": "buy", "entry_price": 45000, "amount": 1}
        result = await strategy.should_exit_position(
            "BTC/USDT",
            current_position,
            44000,  # Current price below stop loss
            pd.DataFrame(),
        )
        assert result is not None
        assert result.signal_type in [SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT]


class TestRiskManager:
    """Test cases for RiskManager class"""

    def test_risk_manager_initialization(self):
        """Test RiskManager initialization"""
        config = {
            "max_position_size": 0.05,
            "max_daily_loss": 0.02,
            "stop_loss_percent": 0.02,
            "take_profit_percent": 0.04,
        }
        risk_manager = RiskManager(config)
        assert risk_manager.max_position_size == 0.05
        assert risk_manager.max_daily_loss == 0.02
        assert risk_manager.stop_loss_percent == 0.02
        assert risk_manager.take_profit_percent == 0.04

    def test_calculate_position_size(self):
        """Test position size calculation"""
        config = {
            "max_position_size": 0.05,
            "max_daily_loss": 0.02,
            "stop_loss_percent": 0.02,
            "take_profit_percent": 0.04,
        }
        risk_manager = RiskManager(config)

        # Test with valid parameters
        position_size = risk_manager.calculate_position_size(
            10000, 45000, 44000, "BTC/USDT"
        )
        assert position_size > 0

        # Test with zero price difference (should return 0)
        position_size = risk_manager.calculate_position_size(
            10000, 45000, 45000, "BTC/USDT"
        )
        assert position_size == 0.0

    def test_calculate_stop_loss(self):
        """Test stop loss calculation"""
        config = {
            "max_position_size": 0.05,
            "max_daily_loss": 0.02,
            "stop_loss_percent": 0.02,
            "take_profit_percent": 0.04,
        }
        risk_manager = RiskManager(config)

        # Test buy side
        stop_loss = risk_manager.calculate_stop_loss(45000, "buy")
        expected = 45000 * (1 - 0.02)
        assert abs(stop_loss - expected) < 0.01

        # Test sell side
        stop_loss = risk_manager.calculate_stop_loss(45000, "sell")
        expected = 45000 * (1 + 0.02)
        assert abs(stop_loss - expected) < 0.01


class TestTechnicalIndicators:
    """Test cases for TechnicalIndicators class"""

    def test_sma(self):
        """Test Simple Moving Average calculation"""
        data = pd.Series([1, 2, 3, 4, 5])
        sma_result = TechnicalIndicators.sma(data, 3)
        assert len(sma_result) == 5
        assert pd.isna(sma_result.iloc[0])  # First values should be NaN
        assert pd.isna(sma_result.iloc[1])  # First values should be NaN
        assert sma_result.iloc[2] == 2.0  # Average of [1,2,3]

    def test_rsi(self, sample_ohlcv_data):
        """Test RSI calculation"""
        rsi_result = TechnicalIndicators.rsi(sample_ohlcv_data["close"])
        assert len(rsi_result) == len(sample_ohlcv_data)
        # RSI should be between 0 and 100 for non-NaN values (first ~14 are NaN)
        valid = rsi_result.dropna()
        assert len(valid) > 0
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_macd(self, sample_ohlcv_data):
        """Test MACD calculation"""
        macd_data = TechnicalIndicators.macd(sample_ohlcv_data["close"])
        assert "macd" in macd_data
        assert "macd_signal" in macd_data
        assert "macd_histogram" in macd_data
        assert len(macd_data["macd"]) == len(sample_ohlcv_data)

    def test_calculate_all_indicators(self, sample_ohlcv_data):
        """Test calculation of all indicators"""
        result_df = calculate_all_indicators(sample_ohlcv_data)
        assert len(result_df) == len(sample_ohlcv_data)
        # Check that some key indicators were added
        assert "rsi" in result_df.columns
        assert "macd" in result_df.columns
        assert "sma_20" in result_df.columns
