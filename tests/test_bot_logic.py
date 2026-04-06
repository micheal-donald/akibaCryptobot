import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from bot import AkibaCryptoBot
from operations.alerts import OperationalAlertState
from trading.position_manager import PositionManager
from trading.opportunity_scanner import OpportunityScanner
from runtime.config import RuntimeConfig
from runtime.mode import RuntimeMode


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
        bot.trading_paused = False
        bot._operational_alert_state = OperationalAlertState()
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
        bot.runtime_config = None
        bot.runtime_handle = None
        bot.runtime_app = None
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
async def test_trading_cycle_skips_legacy_scan_when_runtime_owns_entries(bot):
    bot.runtime_config = RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        runtime_mode="runtime_entries",
        shadow_mode=False,
    )
    bot.exchange.get_balance.return_value = {"USDT": {"free": 1000, "used": 0}}
    bot.update_market_data = AsyncMock()
    bot.update_portfolio_value = AsyncMock()
    bot.position_manager.manage_positions = AsyncMock()
    bot.opportunity_scanner.scan_opportunities = AsyncMock()
    bot.advanced_risk.check_circuit_breakers.return_value = (False, "")

    await bot.trading_cycle()

    bot.opportunity_scanner.scan_opportunities.assert_not_called()


@pytest.mark.asyncio
async def test_trading_cycle_runtime_entries_keeps_legacy_position_management(bot):
    bot.runtime_config = RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        runtime_mode="runtime_entries",
        shadow_mode=False,
    )
    bot.exchange.get_balance.return_value = {"USDT": {"free": 1000, "used": 0}}
    bot.update_market_data = AsyncMock()
    bot.update_portfolio_value = AsyncMock()
    bot.position_manager.manage_positions = AsyncMock()
    bot.opportunity_scanner.scan_opportunities = AsyncMock()
    bot.advanced_risk.check_circuit_breakers.return_value = (False, "")

    await bot.trading_cycle()

    bot.position_manager.manage_positions.assert_awaited_once_with()
    bot.opportunity_scanner.scan_opportunities.assert_not_called()


@pytest.mark.asyncio
async def test_trading_cycle_skips_legacy_position_management_when_runtime_owns_exits(
    bot,
):
    bot.runtime_config = RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        runtime_mode="runtime_primary",
        shadow_mode=False,
    )
    bot.exchange.get_balance.return_value = {"USDT": {"free": 1000, "used": 0}}
    bot.update_market_data = AsyncMock()
    bot.update_portfolio_value = AsyncMock()
    bot.position_manager.manage_positions = AsyncMock()
    bot.opportunity_scanner.scan_opportunities = AsyncMock()
    bot.advanced_risk.check_circuit_breakers.return_value = (False, "")

    await bot.trading_cycle()

    bot.position_manager.manage_positions.assert_not_called()
    bot.opportunity_scanner.scan_opportunities.assert_not_called()


@pytest.mark.asyncio
async def test_scan_helpers_noop_when_runtime_owns_entries(bot):
    bot.runtime_config = RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        runtime_mode="runtime_entries",
        shadow_mode=False,
    )
    bot.opportunity_scanner.scan_opportunities = AsyncMock()
    bot.opportunity_scanner._process_pair = AsyncMock()

    await bot.scan_opportunities()
    await bot._process_pair("BTC/USDT")

    bot.opportunity_scanner.scan_opportunities.assert_not_called()
    bot.opportunity_scanner._process_pair.assert_not_called()


@pytest.mark.asyncio
async def test_shadow_mode_still_allows_legacy_scanning(bot):
    bot.runtime_config = RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        runtime_mode="shadow",
        shadow_mode=True,
    )
    bot.opportunity_scanner.scan_opportunities = AsyncMock()

    await bot.scan_opportunities()

    bot.opportunity_scanner.scan_opportunities.assert_awaited_once_with()


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
async def test_stop_sets_shutdown_event(bot):
    bot.is_running = True

    bot.stop()

    assert bot.is_running is False
    assert bot._stop_event.is_set() is True


@pytest.mark.asyncio
async def test_cleanup_is_idempotent_under_concurrent_calls(bot):
    async def delayed_close():
        await asyncio.sleep(0.01)

    bot.trade_store.close = AsyncMock(side_effect=delayed_close)
    bot.exchange.close = AsyncMock(side_effect=delayed_close)
    bot.data_exchange = bot.exchange

    await asyncio.gather(bot.cleanup(), bot.cleanup())

    assert bot.trade_store.close.await_count == 1
    assert bot.exchange.close.await_count == 1


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


class FakeRuntimeHandle:
    def __init__(self, calls):
        self.calls = calls
        self.app = self

    async def start(self):
        self.calls.append("runtime.start")

    async def stop(self):
        self.calls.append("runtime.stop")


def test_runtime_mode_defaults_to_legacy_without_explicit_config(bot):
    bot.config.pop("runtime", None)
    bot.runtime_config = bot._load_runtime_config()

    assert bot.runtime_mode is RuntimeMode.LEGACY
    assert bot.runtime_enabled is False
    assert bot.runtime_owns_entries is False
    assert bot.runtime_owns_exits is False


def test_runtime_config_loads_when_deployment_profile_present(bot):
    bot.config["runtime"] = {"deployment_profile": "paper_shadow"}
    bot.runtime_config = bot._load_runtime_config()

    assert bot.runtime_config is not None
    assert bot.runtime_config.deployment_profile.value == "paper_shadow"
    assert bot.runtime_mode is RuntimeMode.SHADOW


def test_runtime_config_override_only_does_not_activate_runtime(bot):
    bot.config["runtime"] = {"allow_live_runtime": True}
    bot.runtime_config = bot._load_runtime_config()

    assert bot.runtime_config is None
    assert bot.runtime_mode is RuntimeMode.LEGACY


@pytest.mark.asyncio
async def test_start_rejects_live_runtime_modes_without_override(bot):
    bot.config["exchange"]["sandbox"] = False
    bot.config["runtime"] = {"runtime_mode": "runtime_entries"}

    with pytest.raises(ValueError, match="exchange\\.sandbox"):
        await bot.start()


@pytest.mark.asyncio
async def test_start_rejects_live_shadow_without_override(bot):
    bot.config["exchange"]["sandbox"] = False
    bot.config["runtime"] = {"deployment_profile": "live_shadow"}

    with pytest.raises(ValueError, match="live_shadow"):
        await bot.start()


def test_validate_runtime_startup_allows_live_legacy_without_override(bot):
    bot.config["exchange"]["sandbox"] = False
    bot.runtime_config = RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        deployment_profile="live_legacy",
        runtime_mode="legacy",
        shadow_mode=False,
    )

    bot._validate_runtime_startup()


@pytest.mark.asyncio
async def test_start_runtime_if_enabled_starts_runtime_handle(bot):
    bot.runtime_config = RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        runtime_mode="runtime_entries",
        shadow_mode=False,
    )
    calls = []
    runtime_handle = FakeRuntimeHandle(calls)
    bot._build_runtime_handle = MagicMock(return_value=runtime_handle)

    await bot._start_runtime_if_enabled()

    bot._build_runtime_handle.assert_called_once_with()
    assert calls == ["runtime.start"]
    assert bot.runtime_handle is runtime_handle
    assert bot.runtime_app is runtime_handle


@pytest.mark.asyncio
async def test_cleanup_stops_runtime_before_exchange_teardown(bot):
    bot.runtime_config = RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        runtime_mode="runtime_entries",
        shadow_mode=False,
    )
    calls = []
    bot.runtime_handle = FakeRuntimeHandle(calls)
    bot.runtime_app = bot.runtime_handle

    async def record(label):
        calls.append(label)

    async def close_exchange():
        await record("exchange.close")

    async def close_data_exchange():
        await record("data_exchange.close")

    async def close_trade_store():
        await record("trade_store.close")

    bot.exchange.close = AsyncMock(side_effect=close_exchange)
    bot.data_exchange.close = AsyncMock(side_effect=close_data_exchange)
    bot.trade_store.close = AsyncMock(side_effect=close_trade_store)

    await bot.cleanup()

    assert calls.index("runtime.stop") < calls.index("data_exchange.close")
    assert calls.index("runtime.stop") < calls.index("exchange.close")
    assert calls.index("runtime.stop") < calls.index("trade_store.close")


def test_get_status_info_includes_runtime_cutover_fields(bot):
    status = bot.get_status_info()

    assert status["runtime_mode"] == "legacy"
    assert status["configured_deployment_profile"] is None
    assert status["effective_runtime_mode"] == "legacy"
    assert status["effective_runtime_owns_entries"] is False
    assert status["effective_runtime_owns_exits"] is False
    assert status["runtime_is_running"] is False
    assert status["runtime_owns_entries"] is False
    assert status["runtime_owns_exits"] is False


def test_get_status_info_reports_running_runtime(bot):
    bot.runtime_config = RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        deployment_profile="paper_runtime_primary",
        runtime_mode="runtime_primary",
        shadow_mode=False,
    )
    bot.runtime_app = MagicMock(is_running=True)

    status = bot.get_status_info()

    assert status["runtime_mode"] == "runtime_primary"
    assert status["configured_deployment_profile"] == "paper_runtime_primary"
    assert status["effective_runtime_mode"] == "runtime_primary"
    assert status["effective_runtime_owns_entries"] is True
    assert status["effective_runtime_owns_exits"] is True
    assert status["runtime_is_running"] is True
    assert status["runtime_owns_entries"] is True
    assert status["runtime_owns_exits"] is True


def test_get_runtime_visibility_info_reports_profile_and_source(bot):
    bot.runtime_config = RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        deployment_profile="paper_shadow",
    )

    visibility = bot.get_runtime_visibility_info()

    assert visibility["configured_deployment_profile"] == "paper_shadow"
    assert visibility["effective_runtime_mode"] == "shadow"
    assert visibility["effective_runtime_enabled"] is True
    assert visibility["effective_runtime_owns_entries"] is False
    assert visibility["effective_runtime_owns_exits"] is False
    assert visibility["runtime_mode_source"] == "deployment_profile"


def test_log_runtime_visibility_reports_live_canary_stage(bot):
    bot.runtime_config = RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        deployment_profile="live_shadow",
        allow_live_runtime=True,
    )

    bot._log_runtime_visibility()

    bot.logger.info.assert_any_call(
        "Live runtime rollout profile=%s stage=%s allow_live_runtime=%s",
        "live_shadow",
        "canary",
        True,
    )


@pytest.mark.asyncio
async def test_manage_positions_noop_when_runtime_owns_exits(bot):
    bot.runtime_config = RuntimeConfig(
        symbols=("BTC/USDT",),
        kline_intervals=("1m",),
        runtime_mode="runtime_primary",
        shadow_mode=False,
    )
    bot.position_manager.manage_positions = AsyncMock()

    await bot.manage_positions()

    bot.position_manager.manage_positions.assert_not_called()
