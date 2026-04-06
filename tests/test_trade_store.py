import pytest
import aiosqlite
import os
from datetime import datetime
from persistence.trade_store import TradeStore
from operations.training_jobs import (
    TRAINING_JOB_BLOCKED,
    TRAINING_JOB_QUEUED,
    TRAINING_JOB_SUCCEEDED,
    TrainingJobRequest,
)

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

        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='model_training_jobs'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["name"] == "model_training_jobs"

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


@pytest.mark.asyncio
async def test_training_job_enqueue_and_round_trip(trade_store):
    request = TrainingJobRequest(
        pairs=("BTC/USDT", "ETH/USDT"),
        candles=1200,
        timeframe="1h",
        requested_by="api",
    )

    record = await trade_store.enqueue_training_job(request)

    assert record.status == TRAINING_JOB_QUEUED
    assert record.requested_by == "api"
    assert record.pairs == ("BTC/USDT", "ETH/USDT")
    assert await trade_store.can_start_training() is False

    snapshot = await trade_store.get_training_job_status()
    assert snapshot.current_job is not None
    assert snapshot.current_job.job_id == record.job_id
    assert snapshot.current_job.status == TRAINING_JOB_QUEUED
    assert snapshot.last_job is not None
    assert snapshot.last_job.job_id == record.job_id
    assert snapshot.can_start_training is False
    assert snapshot.active_job_count == 1


@pytest.mark.asyncio
async def test_training_job_blocked_when_active_job_exists(trade_store):
    first = await trade_store.enqueue_training_job(
        TrainingJobRequest(
            pairs=("BTC/USDT",),
            candles=1000,
            timeframe="1h",
            requested_by="telegram",
        )
    )
    assert first.status == TRAINING_JOB_QUEUED

    blocked = await trade_store.enqueue_training_job(
        TrainingJobRequest(
            pairs=("ETH/USDT",),
            candles=500,
            timeframe="4h",
            requested_by="api",
        )
    )

    assert blocked.status == TRAINING_JOB_BLOCKED
    assert blocked.error is not None
    assert "already queued" in blocked.error or "already running" in blocked.error

    active = await trade_store.get_active_training_job()
    assert active is not None
    assert active.job_id == first.job_id

    snapshot = await trade_store.get_training_job_status()
    assert snapshot.can_start_training is False
    assert snapshot.current_job is not None
    assert snapshot.current_job.job_id == first.job_id


@pytest.mark.asyncio
async def test_training_job_persists_and_survives_restart(tmp_path):
    db_path = str(tmp_path / "test_training_job_restart.db")
    store = TradeStore(db_path)
    await store.initialize()

    record = await store.enqueue_training_job(
        TrainingJobRequest(
            pairs=("BNB/USDT",),
            candles=750,
            timeframe="30m",
            requested_by="system",
        )
    )
    await store.mark_training_job_running(record.job_id)
    await store.mark_training_job_succeeded(
        record.job_id,
        promoted_model_paths=("models/BNB_USDT_freqai.joblib",),
        result_summary="Training finished successfully",
        reload_status="reloaded",
    )
    await store.close()

    reopened = TradeStore(db_path)
    await reopened.initialize()

    latest = await reopened.get_latest_training_job()
    assert latest is not None
    assert latest.job_id == record.job_id
    assert latest.status == TRAINING_JOB_SUCCEEDED
    assert latest.promoted_model_paths == ("models/BNB_USDT_freqai.joblib",)
    assert latest.reload_status == "reloaded"
    assert latest.started_at is not None
    assert latest.finished_at is not None
    assert await reopened.can_start_training() is True

    snapshot = await reopened.get_training_job_status()
    assert snapshot.current_job is None
    assert snapshot.last_job is not None
    assert snapshot.last_job.job_id == record.job_id
    assert snapshot.last_job.status == TRAINING_JOB_SUCCEEDED
    await reopened.close()
