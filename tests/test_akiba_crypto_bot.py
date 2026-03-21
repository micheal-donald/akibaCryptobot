import os
import sys
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bot import AkibaCryptoBot
from strategies.base_strategy import SignalType, TradingSignal
from trading.opportunity_scanner import OpportunityScanner
from trading.position_manager import PositionManager


@pytest.fixture
def sample_config():
    return {
        "exchange": {"name": "binance", "sandbox": True},
        "trading": {
            "pairs": ["BTC/USDT", "ETH/USDT"],
            "timeframes": {"primary": "1h", "secondary": "15m"},
            "strategy": "momentum",
            "max_open_trades": 3,
        },
        "risk_management": {
            "max_position_size": 0.05,
            "max_daily_loss": 0.02,
            "stop_loss_percent": 0.02,
            "take_profit_percent": 0.04,
            "max_spread_percent": 0.5,
            "cooldown_candles": 5,
            "trailing_stop": True,
            "trailing_stop_positive": 0.01,
        },
        "strategy_config": {"min_signal_strength": 0.45},
        "persistence": {"db_path": "data/trades.db"},
        "notifications": {"telegram_enabled": False},
        "logging": {"level": "DEBUG"},
    }


@pytest.fixture
def sample_ohlcv_data():
    dates = pd.date_range("2023-01-01", periods=100, freq="1h")
    return pd.DataFrame(
        {
            "open": np.random.uniform(40000, 50000, 100),
            "high": np.random.uniform(41000, 51000, 100),
            "low": np.random.uniform(39000, 49000, 100),
            "close": np.random.uniform(40000, 50000, 100),
            "volume": np.random.uniform(1000, 10000, 100),
        },
        index=dates,
    )


@pytest.fixture
def bot_fixture(sample_config):
    bot = AkibaCryptoBot.__new__(AkibaCryptoBot)
    bot.config = sample_config
    bot.logger = MagicMock()
    bot.exchange = AsyncMock()
    bot.data_exchange = AsyncMock()
    bot.trade_store = AsyncMock()
    bot.risk_manager = MagicMock()
    bot.risk_manager.trailing_stop = True
    bot.risk_manager.positions = {}
    bot.advanced_risk = MagicMock()
    bot.advanced_risk.check_correlation_diversification.return_value = True
    bot.strategy_manager = MagicMock()
    bot.telegram = None
    bot.active_trades = {}
    bot.is_running = False
    bot.start_time = datetime.now()
    bot.session_id = None
    bot.last_update = datetime.now()
    bot._background_tasks = set()
    bot.portfolio_value_history = []
    bot.price_data_cache = {}
    bot.circuit_breaker_active = False
    bot.circuit_breaker_reason = ""
    bot._cached_balance = None
    bot.pair_cooldowns = {}
    bot._stop_event = asyncio.Event()
    bot._cleanup_future = None
    bot.performance_tracker = MagicMock()
    bot.performance_tracker.record_trade_exit = AsyncMock()
    bot.position_manager = PositionManager(bot)
    bot.opportunity_scanner = OpportunityScanner(bot)
    return bot


class TestAkibaCryptoBot:
    @patch("bot.OpportunityScanner")
    @patch("bot.PositionManager")
    @patch("bot.ReconciliationManager")
    @patch("bot.TradeStore")
    @patch("bot.StrategyManager")
    @patch("bot.AdvancedRiskManager")
    @patch("bot.RiskManager")
    @patch("bot.BinanceClient")
    @patch("bot.setup_logging")
    def test_bot_initialization(
        self,
        mock_setup_logging,
        mock_binance_client,
        mock_risk_manager,
        mock_advanced_risk,
        mock_strategy_manager,
        mock_trade_store,
        mock_reconciliation,
        mock_position_manager,
        mock_opportunity_scanner,
        sample_config,
    ):
        mock_logger = Mock()
        mock_setup_logging.return_value = mock_logger
        mock_binance_client.return_value = Mock()

        with patch.object(
            AkibaCryptoBot, "load_config", return_value=sample_config
        ), patch.object(AkibaCryptoBot, "_load_strategies"):
            bot = AkibaCryptoBot("config/config.json")

        assert bot.config == sample_config
        assert bot.logger is mock_logger
        assert mock_binance_client.call_count == 2
        mock_risk_manager.assert_called_once_with(sample_config["risk_management"])
        mock_advanced_risk.assert_called_once_with(sample_config)
        mock_strategy_manager.assert_called_once_with(sample_config)
        mock_trade_store.assert_called_once_with("data/trades.db")
        mock_reconciliation.assert_called_once_with(bot)
        mock_position_manager.assert_called_once_with(bot)
        mock_opportunity_scanner.assert_called_once_with(bot)

    @pytest.mark.asyncio
    async def test_update_market_data_caches_ohlcv(
        self, bot_fixture, sample_ohlcv_data
    ):
        bot_fixture.data_exchange.get_ticker.side_effect = [
            {"last": 45000.0},
            {"last": 3200.0},
        ]
        bot_fixture.data_exchange.get_ohlcv.side_effect = [
            sample_ohlcv_data,
            sample_ohlcv_data.tail(80),
        ]

        await bot_fixture.update_market_data()

        assert set(bot_fixture.price_data_cache) == {"BTC/USDT", "ETH/USDT"}
        assert bot_fixture.data_exchange.get_ticker.await_count == 2
        assert bot_fixture.data_exchange.get_ohlcv.await_count == 2

    @pytest.mark.asyncio
    async def test_process_pair_no_signal_skips_trade(
        self, bot_fixture, sample_ohlcv_data
    ):
        bot_fixture.data_exchange.get_ohlcv.return_value = sample_ohlcv_data
        bot_fixture.data_exchange.get_ticker.return_value = {"last": 45000.0}
        bot_fixture.data_exchange.get_market_depth.return_value = {
            "bids": [[45000.0, 1]],
            "asks": [[45010.0, 1]],
        }
        bot_fixture.strategy_manager.generate_signal = AsyncMock(return_value=None)

        await bot_fixture._process_pair("BTC/USDT")

        bot_fixture.exchange.place_market_order.assert_not_awaited()
        bot_fixture.trade_store.insert_trade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_pair_places_trade(self, bot_fixture, sample_ohlcv_data):
        signal = TradingSignal(
            signal_type=SignalType.BUY,
            strength=0.8,
            price=45000.0,
            timestamp=datetime.now(),
            stop_loss=44100.0,
            take_profit=46800.0,
        )
        bot_fixture.data_exchange.get_ohlcv.return_value = sample_ohlcv_data
        bot_fixture.data_exchange.get_ticker.return_value = {"last": 45000.0}
        bot_fixture.data_exchange.get_market_depth.return_value = {
            "bids": [[45000.0, 1]],
            "asks": [[45005.0, 1]],
        }
        bot_fixture.exchange.get_balance.return_value = {
            "USDT": {"free": 1000, "used": 0}
        }
        bot_fixture.strategy_manager.generate_signal = AsyncMock(return_value=signal)
        bot_fixture.risk_manager.can_open_position.return_value = True
        bot_fixture.risk_manager.calculate_position_size_with_signal.return_value = 0.02
        bot_fixture.risk_manager.calculate_stop_loss.return_value = 44100.0
        bot_fixture.risk_manager.calculate_take_profit.return_value = 46800.0
        bot_fixture.exchange.place_market_order.return_value = {
            "id": "order_123",
            "status": "filled",
            "fee": {"cost": 0.25, "currency": "USDT"},
        }
        bot_fixture.trade_store.insert_trade.return_value = 77

        await bot_fixture._process_pair("BTC/USDT")

        bot_fixture.exchange.place_market_order.assert_awaited_once_with(
            "BTC/USDT", "buy", 0.02, price=45000.0
        )
        assert bot_fixture.active_trades["BTC/USDT"]["db_id"] == 77
        assert bot_fixture.active_trades["BTC/USDT"]["entry_fee"] == pytest.approx(0.25)

    @pytest.mark.asyncio
    async def test_manage_positions_closes_trade_on_exit_signal(
        self, bot_fixture, sample_ohlcv_data
    ):
        bot_fixture.active_trades = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "side": "buy",
                "amount": 0.1,
                "entry_price": 45000.0,
                "stop_loss": 44100.0,
                "take_profit": 46800.0,
                "entry_fee": 0.1,
                "order_id": "entry_1",
            }
        }
        bot_fixture.exchange.get_open_orders.return_value = []
        bot_fixture.data_exchange.get_ticker.return_value = {"last": 46000.0}
        bot_fixture.data_exchange.get_ohlcv.return_value = sample_ohlcv_data
        strategy = MagicMock()
        strategy.should_exit_position = AsyncMock(
            return_value=TradingSignal(
                signal_type=SignalType.CLOSE_LONG,
                strength=1.0,
                price=46000.0,
                timestamp=datetime.now(),
                metadata={"exit_reason": "strategy_exit"},
            )
        )
        bot_fixture.strategy_manager.get_active_strategy.return_value = strategy
        bot_fixture.exchange.place_market_order.return_value = {
            "id": "exit_1",
            "status": "filled",
            "fee": {"cost": 0.2, "currency": "USDT"},
        }
        bot_fixture.risk_manager.remove_position.return_value = {"pnl": 100.0}

        await bot_fixture.manage_positions()

        bot_fixture.exchange.place_market_order.assert_awaited_once_with(
            "BTC/USDT", "sell", 0.1, price=46000.0
        )
        bot_fixture.trade_store.update_trade_exit.assert_awaited_once()
        assert "BTC/USDT" not in bot_fixture.active_trades
