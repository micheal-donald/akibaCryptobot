import pytest
import asyncio
import pandas as pd
import numpy as np
from unittest.mock import Mock, patch, AsyncMock
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from exchange.binance_client import BinanceClient as BinanceConnector


@pytest.fixture
def sample_config():
    """Create sample configuration for testing"""
    return {"api_key": "test_api_key", "api_secret": "test_api_secret", "testnet": True}


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


class TestBinanceConnector:
    """Test cases for BinanceConnector class"""

    @patch("ccxt.binance")
    def test_binance_connector_initialization(self, mock_binance, sample_config):
        """Test BinanceConnector initialization"""
        mock_exchange = Mock()
        mock_binance.return_value = mock_exchange

        connector = BinanceConnector(sample_config)

        # Check that ccxt.binance was called with correct parameters
        mock_binance.assert_called_once_with(
            {
                "apiKey": "test_api_key",
                "secret": "test_api_secret",
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            }
        )

        # Check that testnet was set correctly
        assert connector.testnet == True

    @pytest.mark.asyncio
    @patch("ccxt.binance")
    async def test_fetch_ohlcv(self, mock_binance, sample_config, sample_ohlcv_data):
        """Test fetching OHLCV data"""
        mock_exchange = Mock()
        mock_binance.return_value = mock_exchange

        # Mock the fetch_ohlcv method to return sample data
        mock_exchange.fetch_ohlcv.return_value = [
            [1609459200000, 40000, 41000, 39000, 40500, 1000],
            [1609462800000, 40500, 41500, 39500, 41000, 1100],
            [1609466400000, 41000, 42000, 40000, 41500, 1200],
        ]

        connector = BinanceConnector(sample_config)
        ohlcv_data = await connector.fetch_ohlcv("BTC/USDT", "1h", 3)

        # Check that fetch_ohlcv was called with correct parameters
        mock_exchange.fetch_ohlcv.assert_called_once_with("BTC/USDT", "1h", None, 3)

        # Check that returned data is a DataFrame with correct columns
        assert isinstance(ohlcv_data, pd.DataFrame)
        assert list(ohlcv_data.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
        assert len(ohlcv_data) == 3

    @pytest.mark.asyncio
    @patch("ccxt.binance")
    async def test_create_market_order(self, mock_binance, sample_config):
        """Test creating market order"""
        mock_exchange = Mock()
        mock_binance.return_value = mock_exchange

        # Mock the create_market_order method to return sample response
        mock_response = {
            "id": "123456",
            "symbol": "BTC/USDT",
            "type": "market",
            "side": "buy",
            "amount": 0.1,
            "price": None,
            "cost": 4500,
            "filled": 0.1,
            "remaining": 0.0,
            "status": "closed",
        }
        mock_exchange.create_market_buy_order.return_value = mock_response

        connector = BinanceConnector(sample_config)
        result = await connector.create_market_order("BTC/USDT", "buy", 0.1)

        # Check that create_market_buy_order was called with correct parameters
        mock_exchange.create_market_buy_order.assert_called_once_with("BTC/USDT", 0.1)

        # Check that returned data matches expected response
        assert result == mock_response

    @pytest.mark.asyncio
    @patch("ccxt.binance")
    async def test_create_limit_order(self, mock_binance, sample_config):
        """Test creating limit order"""
        mock_exchange = Mock()
        mock_binance.return_value = mock_exchange

        # Mock the create_limit_order method to return sample response
        mock_response = {
            "id": "123457",
            "symbol": "BTC/USDT",
            "type": "limit",
            "side": "sell",
            "amount": 0.1,
            "price": 46000,
            "cost": 4600,
            "filled": 0.0,
            "remaining": 0.1,
            "status": "open",
        }
        mock_exchange.create_limit_sell_order.return_value = mock_response

        connector = BinanceConnector(sample_config)
        result = await connector.create_limit_order("BTC/USDT", "sell", 0.1, 46000)

        # Check that create_limit_sell_order was called with correct parameters
        mock_exchange.create_limit_sell_order.assert_called_once_with(
            "BTC/USDT", 0.1, 46000
        )

        # Check that returned data matches expected response
        assert result == mock_response

    @pytest.mark.asyncio
    @patch("ccxt.binance")
    async def test_fetch_positions(self, mock_binance, sample_config):
        """Test fetching positions"""
        mock_exchange = Mock()
        mock_binance.return_value = mock_exchange

        # Mock the fetch_positions method to return sample data
        mock_positions = [
            {
                "symbol": "BTC/USDT",
                "side": "long",
                "amount": 0.1,
                "entryPrice": 45000,
                "markPrice": 46000,
                "unrealizedPnl": 1000,
                "percentage": 2.22,
                "initialMargin": 4500,
            }
        ]
        mock_exchange.fetch_positions.return_value = mock_positions

        connector = BinanceConnector(sample_config)
        positions = await connector.fetch_positions(["BTC/USDT"])

        # Check that fetch_positions was called with correct parameters
        mock_exchange.fetch_positions.assert_called_once_with(["BTC/USDT"])

        # Check that returned data matches expected response
        assert positions == mock_positions
        assert len(positions) == 1
        assert positions[0]["symbol"] == "BTC/USDT"

    @pytest.mark.asyncio
    @patch("ccxt.binance")
    async def test_cancel_order(self, mock_binance, sample_config):
        """Test canceling order"""
        mock_exchange = Mock()
        mock_binance.return_value = mock_exchange

        # Mock the cancel_order method to return sample response
        mock_response = {"id": "123456", "symbol": "BTC/USDT", "status": "canceled"}
        mock_exchange.cancel_order.return_value = mock_response

        connector = BinanceConnector(sample_config)
        result = await connector.cancel_order("123456", "BTC/USDT")

        # Check that cancel_order was called with correct parameters
        mock_exchange.cancel_order.assert_called_once_with("123456", "BTC/USDT")

        # Check that returned data matches expected response
        assert result == mock_response
