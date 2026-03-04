import pytest
import pandas as pd
import numpy as np
from unittest.mock import Mock, patch
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from exchange.binance_client import BinanceClient as BinanceConnector


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
    def test_binance_connector_initialization(self, mock_binance):
        """Test BinanceConnector initialization"""
        mock_exchange = Mock()
        mock_binance.return_value = mock_exchange

        connector = BinanceConnector(
            api_key="test_api_key", secret_key="test_api_secret", sandbox=True
        )

        # Check that ccxt.binance was called with correct parameters
        mock_binance.assert_called_once_with(
            {
                "apiKey": "test_api_key",
                "secret": "test_api_secret",
                "sandbox": True,
                "enableRateLimit": True,
                "rateLimit": 1200,
                "options": {
                    "adjustForTimeDifference": True,
                    "warnOnFetchOpenOrdersWithoutSymbol": False,
                    "defaultType": "spot",
                },
            }
        )

        # Check that sandbox was set correctly
        assert connector.sandbox == True

    @pytest.mark.asyncio
    @patch("ccxt.binance")
    async def test_fetch_ohlcv(self, mock_binance, sample_ohlcv_data):
        """Test fetching OHLCV data"""
        mock_exchange = Mock()
        mock_binance.return_value = mock_exchange

        # Mock the fetch_ohlcv method to return sample data
        mock_exchange.fetch_ohlcv.return_value = [
            [1609459200000, 40000, 41000, 39000, 40500, 1000],
            [1609462800000, 40500, 41500, 39500, 41000, 1100],
            [1609466400000, 41000, 42000, 40000, 41500, 1200],
        ]

        connector = BinanceConnector(
            api_key="test_api_key", secret_key="test_api_secret", sandbox=True
        )
        ohlcv_data = await connector.get_ohlcv("BTC/USDT", "1h", 3)

        # Check that fetch_ohlcv was called with correct parameters
        mock_exchange.fetch_ohlcv.assert_called_once_with("BTC/USDT", "1h", limit=3)

        # Check that returned data is a DataFrame with correct columns
        assert isinstance(ohlcv_data, pd.DataFrame)
        assert list(ohlcv_data.columns) == ["open", "high", "low", "close", "volume"]
        assert len(ohlcv_data) == 3

    @pytest.mark.asyncio
    @patch("ccxt.binance")
    async def test_create_market_order(self, mock_binance):
        """Test creating market order"""
        mock_exchange = Mock()
        mock_exchange.markets = {}
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
        mock_exchange.create_market_order.return_value = mock_response

        connector = BinanceConnector(
            api_key="test_api_key", secret_key="test_api_secret", sandbox=True
        )
        result = await connector.place_market_order("BTC/USDT", "buy", 0.1)

        # Check that create_market_order was called with correct parameters
        mock_exchange.create_market_order.assert_called_once_with(
            "BTC/USDT", "buy", 0.1
        )

        # Check that returned data matches expected response
        assert result == mock_response

    @pytest.mark.asyncio
    @patch("ccxt.binance")
    async def test_create_limit_order(self, mock_binance):
        """Test creating limit order"""
        mock_exchange = Mock()
        mock_exchange.markets = {}
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
        mock_exchange.create_limit_order.return_value = mock_response

        connector = BinanceConnector(
            api_key="test_api_key", secret_key="test_api_secret", sandbox=True
        )
        result = await connector.place_limit_order("BTC/USDT", "sell", 0.1, 46000)

        # Check that create_limit_order was called with correct parameters
        mock_exchange.create_limit_order.assert_called_once_with(
            "BTC/USDT", "sell", 0.1, 46000
        )

        # Check that returned data matches expected response
        assert result == mock_response

    @pytest.mark.asyncio
    @patch("ccxt.binanceusdm")
    async def test_fetch_positions(self, mock_binanceusdm):
        """Test fetching futures positions"""
        mock_exchange = Mock()
        mock_binanceusdm.return_value = mock_exchange

        # Mock the fetch_positions method to return sample data
        mock_positions = [
            {
                "symbol": "BTC/USDT",
                "side": "long",
                "contracts": 0.1,
                "entryPrice": 45000,
                "markPrice": 46000,
                "unrealizedPnl": 1000,
                "percentage": 2.22,
                "initialMargin": 4500,
            }
        ]
        mock_exchange.fetch_positions.return_value = mock_positions

        connector = BinanceConnector(
            api_key="test_api_key",
            secret_key="test_api_secret",
            sandbox=True,
            market_type="futures",
        )
        positions = await connector.get_futures_positions("BTC/USDT")

        # Check that fetch_positions was called with correct parameters
        mock_exchange.fetch_positions.assert_called_once_with(symbols=["BTC/USDT"])

        # Check that returned data matches expected response
        assert len(positions) == 1
        assert positions[0]["symbol"] == "BTC/USDT"

    @pytest.mark.asyncio
    @patch("ccxt.binance")
    async def test_cancel_order(self, mock_binance):
        """Test canceling order"""
        mock_exchange = Mock()
        mock_binance.return_value = mock_exchange

        # Mock the cancel_order method to return sample response
        mock_response = {"id": "123456", "symbol": "BTC/USDT", "status": "canceled"}
        mock_exchange.cancel_order.return_value = mock_response

        connector = BinanceConnector(
            api_key="test_api_key", secret_key="test_api_secret", sandbox=True
        )
        result = await connector.cancel_order("123456", "BTC/USDT")

        # Check that cancel_order was called with correct parameters
        mock_exchange.cancel_order.assert_called_once_with("123456", "BTC/USDT")

        # Check that returned data matches expected response
        assert result == mock_response

    def test_validate_and_adjust_order(self):
        """Test the validation and adjustment of orders against market limits"""
        connector = BinanceConnector(
            api_key="test_api_key", secret_key="test_api_secret", sandbox=True
        )
        # Mock the internal ccxt exchange object
        mock_exchange = Mock()
        mock_exchange.markets = {
            "BTC/USDT": {
                "precision": {"amount": 5, "price": 2},
                "limits": {
                    "amount": {"min": 0.0001, "max": 1000.0},
                    "price": {"min": 0.01},
                    "cost": {"min": 10.0},  # minimum notional
                },
            },
            "DOGE/USDT": {
                "precision": {"amount": 0, "price": 5},
                "limits": {
                    "amount": {"min": 1.0, "max": 1000000.0},
                    "price": {"min": 0.00001},
                    "cost": {"min": 5.0},
                },
            },
        }
        mock_exchange.amount_to_precision = lambda sym, amt: (
            f"{amt:.{mock_exchange.markets[sym]['precision']['amount']}f}"
        )
        connector.exchange = mock_exchange
        connector._markets_loaded = True

        # Valid order
        valid, amt = connector.validate_and_adjust_order("BTC/USDT", 0.01, 50000.0)
        assert valid
        assert amt == pytest.approx(0.01)

        # Below min amount
        valid, amt = connector.validate_and_adjust_order("BTC/USDT", 0.00005, 50000.0)
        assert not valid

        # Below min notional (cost < 10)
        valid, amt = connector.validate_and_adjust_order("BTC/USDT", 0.0001, 50000.0)
        assert not valid

        # Valid precision (integer for doge)
        valid, amt = connector.validate_and_adjust_order("DOGE/USDT", 100.5, 0.1)
        assert valid
        assert amt == pytest.approx(100.0)

    @pytest.mark.asyncio
    @patch("ccxt.binance")
    async def test_place_market_order_rejected(self, mock_binance):
        """Test market order is rejected if validation fails"""
        mock_exchange = Mock()
        mock_exchange.markets = {}
        mock_binance.return_value = mock_exchange

        connector = BinanceConnector(
            api_key="test_api_key", secret_key="test_api_secret", sandbox=True
        )

        # Override validate to fail
        with patch.object(
            connector, "validate_and_adjust_order", return_value=(False, 0.0001)
        ):
            order = await connector.place_market_order(
                "BTC/USDT", "buy", 0.0001, price=50000.0
            )

            assert order["status"] == "rejected"
            mock_exchange.create_market_order.assert_not_called()
