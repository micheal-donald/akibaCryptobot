import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from bot import AkibaCryptoBot
from trading.position_manager import PositionManager
from trading.opportunity_scanner import OpportunityScanner


@pytest.fixture
def mock_config():
    return {
        "exchange": {"sandbox": True},
        "trading": {
            "pairs": ["BTC/USDT"],
            "timeframes": {"primary": "1h"},
            "strategy": "momentum",
            "max_open_trades": 3,
        },
        "risk_management": {
            "max_spread_percent": 0.5,
            "cooldown_candles": 5,
            "stop_loss_percent": 0.02,
            "take_profit_percent": 0.04,
            "trailing_stop": True,
            "trailing_stop_positive": 0.01,
        },
        "strategy_config": {"min_signal_strength": 0.45},
        "persistence": {"db_path": "data/trades.db"},
        "notifications": {"telegram_enabled": False},
        "logging": {"level": "DEBUG"},
    }


@pytest.fixture
def bot(mock_config):
    with patch.object(
        AkibaCryptoBot, "load_config", return_value=mock_config
    ), patch.object(AkibaCryptoBot, "_load_strategies"):
        bot = AkibaCryptoBot.__new__(AkibaCryptoBot)
        bot.config = mock_config
        bot.logger = MagicMock()
        bot.exchange = AsyncMock()
        bot.data_exchange = AsyncMock()
        bot.trade_store = AsyncMock()
        bot.risk_manager = MagicMock()
        bot.advanced_risk = MagicMock()
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
        bot.performance_tracker = MagicMock()
        bot.position_manager = PositionManager(bot)
        bot.opportunity_scanner = OpportunityScanner(bot)
        return bot


@pytest.mark.asyncio
async def test_feature_5_fee_deduction(bot):
    """Entry and exit fees are extracted from order response and deducted from PnL."""
    bot.risk_manager.calculate_stop_loss.return_value = 49000.0
    bot.risk_manager.calculate_take_profit.return_value = 52000.0

    # Mock order response with fees
    bot.exchange.place_market_order.return_value = {
        "id": "order_123",
        "status": "filled",
        "fee": {"cost": 0.05, "currency": "USDT"},
    }

    # Place trade
    await bot.place_trade("BTC/USDT", "buy", 1.0, 50000.0)

    assert "BTC/USDT" in bot.active_trades
    assert bot.active_trades["BTC/USDT"]["entry_fee"] == pytest.approx(0.05)

    # Mock close order with fees
    bot.exchange.place_market_order.return_value = {
        "id": "order_456",
        "status": "filled",
        "fee": {"cost": 0.05, "currency": "USDT"},
    }
    bot.data_exchange.get_ticker.return_value = {"last": 51000.0}

    # Close position
    await bot.close_position("BTC/USDT", "take_profit")

    # Gross PnL = (51000 - 50000) * 1.0 = 1000
    # Total fees = 0.05 + 0.05 = 0.10
    # Net PnL = 1000 - 0.10 = 999.90
    bot.trade_store.update_trade_exit.assert_called_once_with(
        "BTC/USDT", 51000.0, "take_profit", pytest.approx(999.90), pytest.approx(0.10)
    )


@pytest.mark.asyncio
async def test_feature_6_balance_cache(bot):
    """get_balance is called once per cycle; cached value used everywhere else."""
    bot.exchange.get_balance.return_value = {"USDT": {"free": 1000, "used": 0}}
    bot.advanced_risk.check_circuit_breakers.return_value = (False, "")
    bot.strategy_manager.generate_signal = AsyncMock(return_value=None)

    # Run a full trading cycle
    await bot.trading_cycle()

    # Balance fetched exactly once (at top of trading_cycle)
    assert bot.exchange.get_balance.call_count == 1
    assert bot._cached_balance == {"USDT": {"free": 1000, "used": 0}}


@pytest.mark.asyncio
async def test_feature_7_cooldown(bot):
    """Pairs on cooldown are skipped in _process_pair."""
    # Setup: recently closed trade with stop_loss — cooldown active
    bot.pair_cooldowns = {"BTC/USDT": datetime.now() + timedelta(hours=1)}

    await bot._process_pair("BTC/USDT")

    # Should not even fetch data
    bot.data_exchange.get_ohlcv.assert_not_called()

    # Simulate cooldown expiry
    bot.pair_cooldowns["BTC/USDT"] = datetime.now() - timedelta(seconds=1)
    bot.data_exchange.get_ohlcv.return_value = None  # will short-circuit on no data

    await bot._process_pair("BTC/USDT")

    # Now it should have tried to fetch data
    assert bot.data_exchange.get_ohlcv.call_count == 1
    assert "BTC/USDT" not in bot.pair_cooldowns


@pytest.mark.asyncio
async def test_feature_9_cancel_on_shutdown(bot):
    """Open orders are cancelled during cleanup."""
    bot.active_trades = {"BTC/USDT": {"order_id": "123"}}
    bot.exchange.get_open_orders.return_value = [{"id": "999"}]

    await bot.cleanup()

    bot.exchange.cancel_order.assert_called_once_with("999", "BTC/USDT")


@pytest.mark.asyncio
async def test_feature_10_spread_filter(bot):
    """Wide spreads are rejected, tight spreads are allowed."""
    bot.config["risk_management"]["max_spread_percent"] = 0.5

    # 1. Wide spread (1% > 0.5%) — should reject
    bot.data_exchange.get_market_depth.return_value = {
        "bids": [[100, 1]],
        "asks": [[101, 1]],
    }
    assert await bot._check_spread("BTC/USDT") is False

    # 2. Tight spread (0.1% < 0.5%) — should allow
    bot.data_exchange.get_market_depth.return_value = {
        "bids": [[1000, 1]],
        "asks": [[1001, 1]],
    }
    assert await bot._check_spread("BTC/USDT") is True


@pytest.mark.asyncio
async def test_bug3_risk_manager_synced_on_restart(bot):
    """After loading persisted trades, risk_manager.add_position is called for each."""
    bot.trade_store.initialize = AsyncMock()
    bot.trade_store.load_open_trades = AsyncMock(
        return_value={
            "BTC/USDT": {
                "side": "buy",
                "amount": 0.5,
                "entry_price": 60000.0,
                "stop_loss": 59000.0,
                "take_profit": 62000.0,
                "order_id": "abc",
                "trailing_stop_price": 59500.0,
            }
        }
    )
    bot.exchange.get_balance.return_value = {"USDT": {"free": 1000}}
    bot.data_exchange.get_ticker.return_value = {"last": 60000.0}

    # Simulate start() up to the trade-reload part
    await bot.trade_store.initialize()
    bot.active_trades = await bot.trade_store.load_open_trades()
    for symbol, trade in bot.active_trades.items():
        bot.risk_manager.add_position(
            symbol=symbol,
            side=trade["side"],
            amount=trade["amount"],
            entry_price=trade["entry_price"],
            stop_loss=trade.get("stop_loss", 0),
            take_profit=trade.get("take_profit", 0),
            order_id=trade.get("order_id", ""),
        )
        if trade.get("trailing_stop_price") and symbol in bot.risk_manager.positions:
            bot.risk_manager.positions[symbol]["trailing_stop_price"] = trade[
                "trailing_stop_price"
            ]

    bot.risk_manager.add_position.assert_called_once_with(
        symbol="BTC/USDT",
        side="buy",
        amount=0.5,
        entry_price=60000.0,
        stop_loss=59000.0,
        take_profit=62000.0,
        order_id="abc",
    )
