import pytest
import aiosqlite
import os
from datetime import datetime
from persistence.trade_store import TradeStore

import pytest_asyncio


@pytest_asyncio.fixture
async def trade_store(tmp_path):
    """Fixture to create and clean up an in-memory or temp SQLite TradeStore"""
    db_path = str(tmp_path / "test_trades.db")
    store = TradeStore(db_path)
    await store.initialize()
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_initialize(tmp_path):
    """Test table creation on initialize"""
    db_path = str(tmp_path / "test_init.db")
    store = TradeStore(db_path)
    await store.initialize()

    assert os.path.exists(db_path)
    # Check if table was created
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["name"] == "trades"

    await store.close()


@pytest.mark.asyncio
async def test_insert_and_load_open_trades(trade_store):
    """Test round-trip insert and load"""
    trade = {
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": 0.1,
        "entry_price": 50000.0,
        "stop_loss": 49000.0,
        "take_profit": 52000.0,
        "timestamp": datetime.now().isoformat(),
        "order_id": "test_order_1",
    }

    row_id = await trade_store.insert_trade(trade)
    assert row_id == 1

    trades = await trade_store.load_open_trades()
    assert len(trades) == 1
    assert "BTC/USDT" in trades

    loaded_trade = trades["BTC/USDT"]
    assert loaded_trade["db_id"] == 1
    assert loaded_trade["side"] == "buy"
    assert loaded_trade["amount"] == 0.1
    assert loaded_trade["status"] == "open"


@pytest.mark.asyncio
async def test_update_trade_exit(trade_store):
    """Test marking trade as closed and setting exit fields"""
    # Insert first
    trade = {
        "symbol": "ETH/USDT",
        "side": "sell",
        "amount": 1.0,
        "entry_price": 3000.0,
        "timestamp": datetime.now().isoformat(),
    }
    await trade_store.insert_trade(trade)

    # Verify open
    open_trades = await trade_store.load_open_trades()
    assert "ETH/USDT" in open_trades

    # Mark exit
    await trade_store.update_trade_exit(
        "ETH/USDT", exit_price=2900.0, exit_reason="take_profit", pnl=100.0, fees=0.5
    )

    # Verify empty from open trades view
    open_trades = await trade_store.load_open_trades()
    assert "ETH/USDT" not in open_trades

    # Directly query DB to check exit fields
    async with aiosqlite.connect(trade_store.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM trades WHERE symbol='ETH/USDT'")
        row = await cursor.fetchone()

        assert row["status"] == "closed"
        assert row["exit_price"] == 2900.0
        assert row["exit_reason"] == "take_profit"
        assert row["pnl"] == 100.0
        assert row["fees"] == 0.5
        assert row["exit_timestamp"] is not None


@pytest.mark.asyncio
async def test_update_trailing_stop(trade_store):
    """Test updating trailing stop price"""
    trade = {
        "symbol": "BNB/USDT",
        "side": "buy",
        "amount": 5.0,
        "entry_price": 400.0,
        "timestamp": datetime.now().isoformat(),
    }
    await trade_store.insert_trade(trade)

    await trade_store.update_trailing_stop("BNB/USDT", 410.0)

    open_trades = await trade_store.load_open_trades()
    assert open_trades["BNB/USDT"]["trailing_stop_price"] == 410.0


@pytest.mark.asyncio
async def test_end_session_noops_after_close(tmp_path):
    db_path = str(tmp_path / "test_end_session_after_close.db")
    store = TradeStore(db_path)
    await store.initialize()
    session_id = await store.start_session(strategy="ensemble", trading_mode="paper")
    await store.close()

    await store.end_session(session_id)
