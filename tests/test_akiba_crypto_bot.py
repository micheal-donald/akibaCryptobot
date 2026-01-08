import pytest
import asyncio
import pandas as pd
import numpy as np
from unittest.mock import Mock, patch, AsyncMock
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bot import AkibaCryptoBot


@pytest.fixture
def sample_config():
    """Create sample configuration for testing"""
    return {
        "exchange": {
            "name": "binance",
            "api_key": "test_key",
            "api_secret": "test_secret",
            "testnet": True,
        },
        "symbols": ["BTC/USDT", "ETH/USDT"],
        "timeframe": "1h",
        "risk_management": {
            "max_position_size": 0.05,
            "max_daily_loss": 0.02,
            "stop_loss_percent": 0.02,
            "take_profit_percent": 0.04,
        },
        "strategy": {
            "name": "momentum",
            "min_signal_strength": 0.6,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "adx_threshold": 25,
        },
    }


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


class TestAkibaCryptoBot:
    """Test cases for AkibaCryptoBot class"""

    @patch("akiba_crypto_bot.BinanceConnector")
    @patch("akiba_crypto_bot.MomentumStrategy")
    @patch("akiba_crypto_bot.RiskManager")
    @patch("akiba_crypto_bot.PortfolioManager")
    def test_bot_initialization(
        self,
        mock_portfolio_manager,
        mock_risk_manager,
        mock_strategy,
        mock_connector,
        sample_config,
    ):
        """Test AkibaCryptoBot initialization"""
        # Create mock instances
        mock_connector_instance = Mock()
        mock_strategy_instance = Mock()
        mock_risk_manager_instance = Mock()
        mock_portfolio_manager_instance = Mock()

        mock_connector.return_value = mock_connector_instance
        mock_strategy.return_value = mock_strategy_instance
        mock_risk_manager.return_value = mock_risk_manager_instance
        mock_portfolio_manager.return_value = mock_portfolio_manager_instance

        # Create bot instance
        bot = AkibaCryptoBot(sample_config)

        # Check that components were initialized
        assert bot.config == sample_config
        assert bot.symbols == ["BTC/USDT", "ETH/USDT"]
        assert bot.timeframe == "1h"
        assert bot.connector == mock_connector_instance
        assert bot.strategy == mock_strategy_instance
        assert bot.risk_manager == mock_risk_manager_instance
        assert bot.portfolio_manager == mock_portfolio_manager_instance

    @pytest.mark.asyncio
    @patch("akiba_crypto_bot.BinanceConnector")
    @patch("akiba_crypto_bot.MomentumStrategy")
    async def test_fetch_market_data(
        self, mock_strategy, mock_connector, sample_config, sample_ohlcv_data
    ):
        """Test fetching market data"""
        # Create mock instances
        mock_connector_instance = Mock()
        mock_strategy_instance = Mock()

        mock_connector.return_value = mock_connector_instance
        mock_strategy.return_value = mock_strategy_instance

        # Mock the fetch_ohlcv method
        mock_connector_instance.fetch_ohlcv.return_value = sample_ohlcv_data

        # Create bot instance
        bot = AkibaCryptoBot(sample_config)

        # Test fetching market data
        ohlcv_data = await bot.fetch_market_data("BTC/USDT")

        # Check that fetch_ohlcv was called with correct parameters
        mock_connector_instance.fetch_ohlcv.assert_called_once_with(
            "BTC/USDT", "1h", 200
        )

        # Check that returned data matches expected data
        assert isinstance(ohlcv_data, pd.DataFrame)
        assert len(ohlcv_data) == len(sample_ohlcv_data)

    @pytest.mark.asyncio
    @patch("akiba_crypto_bot.BinanceConnector")
    @patch("akiba_crypto_bot.MomentumStrategy")
    @patch("akiba_crypto_bot.RiskManager")
    async def test_execute_trade_no_signal(
        self, mock_risk_manager, mock_strategy, mock_connector, sample_config
    ):
        """Test executing trade with no signal"""
        # Create mock instances
        mock_connector_instance = Mock()
        mock_strategy_instance = Mock()
        mock_risk_manager_instance = Mock()

        mock_connector.return_value = mock_connector_instance
        mock_strategy.return_value = mock_strategy_instance
        mock_risk_manager.return_value = mock_risk_manager_instance

        # Mock the strategy to return None (no signal)
        mock_strategy_instance.generate_signal.return_value = None

        # Create bot instance
        bot = AkibaCryptoBot(sample_config)

        # Test executing trade
        result = await bot.execute_trade("BTC/USDT")

        # Check that no trade was executed
        assert result is None
        mock_strategy_instance.generate_signal.assert_called_once()
        mock_connector_instance.create_market_order.assert_not_called()

    @pytest.mark.asyncio
    @patch("akiba_crypto_bot.BinanceConnector")
    @patch("akiba_crypto_bot.MomentumStrategy")
    @patch("akiba_crypto_bot.RiskManager")
    @patch("akiba_crypto_bot.PortfolioManager")
    async def test_execute_trade_with_signal(
        self,
        mock_portfolio_manager,
        mock_risk_manager,
        mock_strategy,
        mock_connector,
        sample_config,
        sample_ohlcv_data,
    ):
        """Test executing trade with valid signal"""
        # Create mock instances
        mock_connector_instance = Mock()
        mock_strategy_instance = Mock()
        mock_risk_manager_instance = Mock()
        mock_portfolio_manager_instance = Mock()

        mock_connector.return_value = mock_connector_instance
        mock_strategy.return_value = mock_strategy_instance
        mock_risk_manager.return_value = mock_risk_manager_instance
        mock_portfolio_manager.return_value = mock_portfolio_manager_instance

        # Mock the strategy to return a buy signal
        mock_signal = Mock()
        mock_signal.signal_type.name = "BUY"
        mock_signal.price = 45000
        mock_strategy_instance.generate_signal.return_value = mock_signal

        # Mock risk manager to return valid position size
        mock_risk_manager_instance.calculate_position_size.return_value = 0.1

        # Mock connector to return successful order
        mock_order_response = {
            "id": "123456",
            "symbol": "BTC/USDT",
            "type": "market",
            "side": "buy",
            "amount": 0.1,
            "price": 45000,
            "cost": 4500,
            "filled": 0.1,
            "remaining": 0.0,
            "status": "closed",
        }
        mock_connector_instance.create_market_order.return_value = mock_order_response

        # Create bot instance
        bot = AkibaCryptoBot(sample_config)

        # Test executing trade
        result = await bot.execute_trade("BTC/USDT")

        # Check that trade was executed
        assert result == mock_order_response
        mock_strategy_instance.generate_signal.assert_called_once()
        mock_risk_manager_instance.calculate_position_size.assert_called_once()
        mock_connector_instance.create_market_order.assert_called_once_with(
            "BTC/USDT", "buy", 0.1
        )

    @pytest.mark.asyncio
    @patch("akiba_crypto_bot.BinanceConnector")
    @patch("akiba_crypto_bot.MomentumStrategy")
    @patch("akiba_crypto_bot.RiskManager")
    async def test_manage_positions(
        self, mock_risk_manager, mock_strategy, mock_connector, sample_config
    ):
        """Test managing positions"""
        # Create mock instances
        mock_connector_instance = Mock()
        mock_strategy_instance = Mock()
        mock_risk_manager_instance = Mock()

        mock_connector.return_value = mock_connector_instance
        mock_strategy.return_value = mock_strategy_instance
        mock_risk_manager.return_value = mock_risk_manager_instance

        # Mock current positions
        mock_positions = [
            {
                "symbol": "BTC/USDT",
                "side": "long",
                "amount": 0.1,
                "entryPrice": 45000,
                "markPrice": 44000,  # Price moved against us
            }
        ]
        mock_connector_instance.fetch_positions.return_value = mock_positions

        # Mock strategy to return exit signal
        mock_exit_signal = Mock()
        mock_exit_signal.signal_type.name = "CLOSE_LONG"
        mock_strategy_instance.should_exit_position.return_value = mock_exit_signal

        # Mock connector to return successful order
        mock_order_response = {
            "id": "123457",
            "symbol": "BTC/USDT",
            "type": "market",
            "side": "sell",
            "amount": 0.1,
            "price": 44000,
            "cost": 4400,
            "filled": 0.1,
            "remaining": 0.0,
            "status": "closed",
        }
        mock_connector_instance.create_market_order.return_value = mock_order_response

        # Create bot instance
        bot = AkibaCryptoBot(sample_config)

        # Test managing positions
        result = await bot.manage_positions()

        # Check that position was closed
        assert len(result) == 1
        assert result[0] == mock_order_response
        mock_connector_instance.fetch_positions.assert_called_once()
        mock_strategy_instance.should_exit_position.assert_called_once()
        mock_connector_instance.create_market_order.assert_called_once_with(
            "BTC/USDT", "sell", 0.1
        )
