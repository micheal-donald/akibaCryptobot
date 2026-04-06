"""
Async SQLite-backed trade store.

Replaces the in-memory ``self.active_trades = {}`` dictionary so that
open positions survive bot restarts and crashes.
"""

from __future__ import annotations

import asyncio
import json
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional, Sequence
from uuid import uuid4

import aiosqlite

from operations.training_jobs import (
    ACTIVE_TRAINING_JOB_STATUSES,
    TRAINING_JOB_BLOCKED,
    TRAINING_JOB_FAILED,
    TRAINING_JOB_QUEUED,
    TRAINING_JOB_RUNNING,
    TRAINING_JOB_SUCCEEDED,
    TrainingJobRecord,
    TrainingJobRequest,
    TrainingJobSnapshot,
)

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    side            TEXT    NOT NULL,
    amount          REAL    NOT NULL,
    entry_price     REAL    NOT NULL,
    stop_loss       REAL,
    take_profit     REAL,
    timestamp       INTEGER NOT NULL,
    order_id        TEXT,
    status          TEXT    NOT NULL DEFAULT 'open',
    exit_price      REAL,
    exit_timestamp  INTEGER,
    exit_reason     TEXT,
    pnl             REAL,
    fees            REAL    DEFAULT 0.0,
    trailing_stop_price REAL
)
"""

CREATE_SESSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS bot_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time      INTEGER NOT NULL,
    end_time        INTEGER,
    uptime_seconds  REAL,
    exit_reason     TEXT    DEFAULT 'unknown',
    strategy        TEXT,
    trading_mode    TEXT
)
"""

CREATE_PORTFOLIO_HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_history (
    timestamp       INTEGER PRIMARY KEY,
    total_value     REAL    NOT NULL
)
"""

CREATE_TRAINING_JOBS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS model_training_jobs (
    job_id              TEXT    PRIMARY KEY,
    status              TEXT    NOT NULL,
    requested_by        TEXT    NOT NULL,
    requested_at        INTEGER NOT NULL,
    started_at          INTEGER,
    finished_at         INTEGER,
    pairs               TEXT    NOT NULL,
    candles             INTEGER NOT NULL,
    timeframe           TEXT    NOT NULL,
    progress            REAL    NOT NULL DEFAULT 0.0,
    result_summary      TEXT,
    error               TEXT,
    promoted_model_paths TEXT,
    reload_status       TEXT
)
"""


class TradeStore:
    """Thin async wrapper around an SQLite database for trade persistence."""

    def __init__(self, db_path: str = "data/trades.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        self._training_job_lock: asyncio.Lock | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self):
        """Create the database directory and table if they don't exist."""
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(CREATE_TABLE_SQL)
        await self._db.execute(CREATE_SESSIONS_TABLE_SQL)
        await self._db.execute(CREATE_PORTFOLIO_HISTORY_TABLE_SQL)
        await self._db.execute(CREATE_TRAINING_JOBS_TABLE_SQL)
        # Time-series optimizations: indices for fast range scans and filtering
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_status_exit ON trades(status, exit_timestamp);"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_status_symbol ON trades(status, symbol);"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_training_jobs_status_requested_at ON model_training_jobs(status, requested_at);"
        )
        await self._db.commit()
        logger.info(f"TradeStore initialised → {self.db_path}")

    async def close(self):
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("TradeStore closed")

    def _parse_timestamp(self, ts) -> Optional[datetime]:
        """Robustly parse a timestamp that could be an ISO string or a Unix integer."""
        if ts is None:
            return None
        if isinstance(ts, str):
            # Try Unix epoch first (in case it's string-encoded int)
            try:
                return datetime.fromtimestamp(int(ts))
            except (ValueError, TypeError):
                # Fallback to ISO parsing
                try:
                    return datetime.fromisoformat(ts)
                except ValueError:
                    logger.error(f"Failed to parse timestamp: {ts}")
                    return datetime.now()
        elif isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts)
        return datetime.now()

    @staticmethod
    def _serialize_json(values: Sequence[str]) -> str:
        return json.dumps(list(values))

    @staticmethod
    def _deserialize_json(values: str | None) -> tuple[str, ...]:
        if not values:
            return ()
        try:
            decoded = json.loads(values)
        except json.JSONDecodeError:
            return ()
        if not isinstance(decoded, list):
            return ()
        return tuple(str(value) for value in decoded if str(value))

    @staticmethod
    def _timestamp_or_none(value: int | float | None) -> datetime | None:
        if value is None:
            return None
        return datetime.fromtimestamp(value)

    def _training_job_mutex(self) -> asyncio.Lock:
        if self._training_job_lock is None:
            self._training_job_lock = asyncio.Lock()
        return self._training_job_lock

    async def _fetch_training_job_row(
        self, query: str, params: Sequence[object] = ()
    ) -> Optional[aiosqlite.Row]:
        cursor = await self._db.execute(query, params)
        return await cursor.fetchone()

    def _row_to_training_job(
        self, row: aiosqlite.Row | None
    ) -> TrainingJobRecord | None:
        if row is None:
            return None
        return TrainingJobRecord(
            job_id=row["job_id"],
            status=row["status"],
            requested_by=row["requested_by"],
            requested_at=self._timestamp_or_none(row["requested_at"]),
            started_at=self._timestamp_or_none(row["started_at"]),
            finished_at=self._timestamp_or_none(row["finished_at"]),
            pairs=self._deserialize_json(row["pairs"]),
            candles=row["candles"],
            timeframe=row["timeframe"],
            progress=row["progress"],
            result_summary=row["result_summary"],
            error=row["error"],
            promoted_model_paths=self._deserialize_json(row["promoted_model_paths"]),
            reload_status=row["reload_status"],
        )

    async def get_latest_training_job(self) -> TrainingJobRecord | None:
        """Return the most recent training job, if one exists."""
        row = await self._fetch_training_job_row(
            """
            SELECT *
              FROM model_training_jobs
          ORDER BY requested_at DESC, rowid DESC
             LIMIT 1
            """
        )
        return self._row_to_training_job(row)

    async def get_latest_successful_training_job(self) -> TrainingJobRecord | None:
        """Return the most recent successful training job, if one exists."""
        row = await self._fetch_training_job_row(
            """
            SELECT *
              FROM model_training_jobs
             WHERE status = ?
          ORDER BY finished_at DESC, requested_at DESC, rowid DESC
             LIMIT 1
            """,
            (TRAINING_JOB_SUCCEEDED,),
        )
        return self._row_to_training_job(row)

    async def get_active_training_job(self) -> TrainingJobRecord | None:
        """Return the active queued/running training job, if one exists."""
        row = await self._fetch_training_job_row(
            """
            SELECT *
              FROM model_training_jobs
             WHERE status IN (?, ?)
          ORDER BY requested_at DESC, rowid DESC
             LIMIT 1
            """,
            tuple(ACTIVE_TRAINING_JOB_STATUSES),
        )
        return self._row_to_training_job(row)

    async def can_start_training(self) -> bool:
        """Return True when no queued/running training job exists."""
        return (await self.get_active_training_job()) is None

    async def get_training_job_status(self) -> TrainingJobSnapshot:
        """Return a snapshot of the current and most recent training jobs."""
        current_job = await self.get_active_training_job()
        last_job = await self.get_latest_training_job()
        return TrainingJobSnapshot(
            current_job=current_job,
            last_job=last_job,
            can_start_training=current_job is None,
            active_job_count=1 if current_job is not None else 0,
        )

    async def enqueue_training_job(
        self,
        request: TrainingJobRequest | Dict[str, object],
        *,
        job_id: str | None = None,
    ) -> TrainingJobRecord:
        """Create a queued training job or persist a blocked request."""
        if isinstance(request, dict):
            request = TrainingJobRequest(
                pairs=tuple(request.get("pairs", ()) or ()),
                candles=request.get("candles", 1000),
                timeframe=request.get("timeframe", "1h"),
                requested_by=request.get("requested_by", "system"),
            )

        async with self._training_job_mutex():
            active_job = await self.get_active_training_job()
            record = TrainingJobRecord(
                job_id=job_id or uuid4().hex,
                status=(
                    TRAINING_JOB_QUEUED if active_job is None else TRAINING_JOB_BLOCKED
                ),
                requested_by=request.requested_by,
                requested_at=datetime.now(),
                pairs=request.pairs,
                candles=request.candles,
                timeframe=request.timeframe,
                progress=0.0,
                result_summary=(
                    "Queued for execution"
                    if active_job is None
                    else f"Blocked by active training job {active_job.job_id}"
                ),
                error=(
                    None
                    if active_job is None
                    else f"training job {active_job.job_id} is already {active_job.status}"
                ),
                reload_status="pending" if active_job is None else "blocked",
            )
            await self._db.execute(
                """
                INSERT INTO model_training_jobs (
                    job_id,
                    status,
                    requested_by,
                    requested_at,
                    started_at,
                    finished_at,
                    pairs,
                    candles,
                    timeframe,
                    progress,
                    result_summary,
                    error,
                    promoted_model_paths,
                    reload_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.job_id,
                    record.status,
                    record.requested_by,
                    (
                        int(record.requested_at.timestamp())
                        if record.requested_at
                        else None
                    ),
                    None,
                    None,
                    self._serialize_json(record.pairs),
                    record.candles,
                    record.timeframe,
                    record.progress,
                    record.result_summary,
                    record.error,
                    self._serialize_json(record.promoted_model_paths),
                    record.reload_status,
                ),
            )
            await self._db.commit()
            return record

    async def update_training_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        progress: float | None = None,
        result_summary: str | None = None,
        error: str | None = None,
        promoted_model_paths: Sequence[str] | None = None,
        reload_status: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> TrainingJobRecord | None:
        """Update an existing training job and return the refreshed row."""
        async with self._training_job_mutex():
            updates: list[str] = []
            params: list[object] = []
            if status is not None:
                updates.append("status = ?")
                params.append(status)
            if progress is not None:
                updates.append("progress = ?")
                params.append(float(progress))
            if result_summary is not None:
                updates.append("result_summary = ?")
                params.append(result_summary)
            if error is not None:
                updates.append("error = ?")
                params.append(error)
            if promoted_model_paths is not None:
                updates.append("promoted_model_paths = ?")
                params.append(self._serialize_json(tuple(promoted_model_paths)))
            if reload_status is not None:
                updates.append("reload_status = ?")
                params.append(reload_status)
            if started_at is not None:
                updates.append("started_at = ?")
                params.append(int(started_at.timestamp()))
            if finished_at is not None:
                updates.append("finished_at = ?")
                params.append(int(finished_at.timestamp()))

            if not updates:
                return await self.get_latest_training_job()

            params.append(job_id)
            await self._db.execute(
                f"""
                UPDATE model_training_jobs
                   SET {", ".join(updates)}
                 WHERE job_id = ?
                """,
                tuple(params),
            )
            await self._db.commit()
            return await self.get_training_job_by_id(job_id)

    async def get_training_job_by_id(self, job_id: str) -> TrainingJobRecord | None:
        """Return a training job by id."""
        row = await self._fetch_training_job_row(
            "SELECT * FROM model_training_jobs WHERE job_id = ? LIMIT 1",
            (job_id,),
        )
        return self._row_to_training_job(row)

    async def mark_training_job_running(self, job_id: str) -> TrainingJobRecord | None:
        """Mark a queued job as running."""
        return await self.update_training_job(
            job_id,
            status=TRAINING_JOB_RUNNING,
            started_at=datetime.now(),
            reload_status="running",
        )

    async def mark_training_job_succeeded(
        self,
        job_id: str,
        *,
        promoted_model_paths: Sequence[str] | None = None,
        result_summary: str | None = None,
        reload_status: str = "succeeded",
    ) -> TrainingJobRecord | None:
        """Mark a training job as succeeded."""
        return await self.update_training_job(
            job_id,
            status=TRAINING_JOB_SUCCEEDED,
            result_summary=result_summary,
            promoted_model_paths=promoted_model_paths,
            reload_status=reload_status,
            finished_at=datetime.now(),
        )

    async def mark_training_job_failed(
        self,
        job_id: str,
        *,
        error: str,
        result_summary: str | None = None,
        reload_status: str = "failed",
    ) -> TrainingJobRecord | None:
        """Mark a training job as failed."""
        return await self.update_training_job(
            job_id,
            status=TRAINING_JOB_FAILED,
            error=error,
            result_summary=result_summary,
            reload_status=reload_status,
            finished_at=datetime.now(),
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def load_open_trades(self) -> Dict[str, Dict]:
        """Return all open trades as a dict keyed by symbol."""
        cursor = await self._db.execute("SELECT * FROM trades WHERE status = 'open'")
        rows = await cursor.fetchall()
        trades: Dict[str, Dict] = {}
        for row in rows:
            symbol = row["symbol"]
            trades[symbol] = {
                "db_id": row["id"],
                "symbol": symbol,
                "side": row["side"],
                "amount": row["amount"],
                "entry_price": row["entry_price"],
                "stop_loss": row["stop_loss"],
                "take_profit": row["take_profit"],
                "timestamp": self._parse_timestamp(row["timestamp"]),
                "order_id": row["order_id"],
                "status": row["status"],
                "fees": row["fees"],
                "trailing_stop_price": row["trailing_stop_price"],
            }
        logger.info(f"Loaded {len(trades)} open trade(s) from database")
        return trades

    async def load_closed_trades(self) -> List[Dict]:
        """Return all closed trades ordered by exit time."""
        cursor = await self._db.execute(
            "SELECT * FROM trades WHERE status = 'closed' ORDER BY exit_timestamp ASC"
        )
        rows = await cursor.fetchall()
        trades = []
        for row in rows:
            trades.append(
                {
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "amount": row["amount"],
                    "entry_price": row["entry_price"],
                    "exit_price": row["exit_price"],
                    "timestamp": self._parse_timestamp(row["timestamp"]),
                    "exit_timestamp": (
                        self._parse_timestamp(row["exit_timestamp"])
                        if row["exit_timestamp"]
                        else None
                    ),
                    "exit_reason": row["exit_reason"],
                    "pnl": row["pnl"],
                    "fees": row["fees"] or 0.0,
                }
            )
        logger.info(f"Loaded {len(trades)} closed trade(s) from database")
        return trades

    async def load_portfolio_history(self, limit: int = 1000) -> List[Dict]:
        """Return the latest portfolio snapshots."""
        cursor = await self._db.execute(
            "SELECT * FROM portfolio_history ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()

        return [
            {
                "timestamp": self._parse_timestamp(r["timestamp"]),
                "total_value": r["total_value"],
            }
            for r in reversed(rows)
        ]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def insert_trade(self, trade: Dict) -> int:
        """Insert a new open trade and return the row id."""
        ts_int = int(datetime.now().timestamp())

        cursor = await self._db.execute(
            """
            INSERT INTO trades
                (symbol, side, amount, entry_price, stop_loss, take_profit,
                 timestamp, order_id, status, fees, trailing_stop_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                trade["symbol"],
                trade["side"],
                trade["amount"],
                trade["entry_price"],
                trade.get("stop_loss"),
                trade.get("take_profit"),
                ts_int,
                trade.get("order_id"),
                trade.get("entry_fee", trade.get("fees", 0.0)),
                trade.get("trailing_stop_price"),
            ),
        )
        await self._db.commit()
        row_id = cursor.lastrowid
        logger.info(f"Persisted trade #{row_id} for {trade['symbol']}")
        return row_id

    async def insert_portfolio_snapshot(self, total_value: float):
        """Insert a new portfolio value snapshot."""
        now_ts = int(datetime.now().timestamp())
        await self._db.execute(
            "INSERT OR REPLACE INTO portfolio_history (timestamp, total_value) VALUES (?, ?)",
            (now_ts, total_value),
        )
        await self._db.commit()

    async def update_trade_exit(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str,
        pnl: float,
        fees: float = 0.0,
    ):
        """Mark the open trade for *symbol* as closed."""
        await self._db.execute(
            """
            UPDATE trades
               SET status         = 'closed',
                   exit_price     = ?,
                   exit_timestamp = ?,
                   exit_reason    = ?,
                   pnl            = ?,
                   fees           = ?
             WHERE symbol = ? AND status = 'open'
            """,
            (
                exit_price,
                int(datetime.now().timestamp()),
                exit_reason,
                pnl,
                fees,
                symbol,
            ),
        )
        await self._db.commit()
        logger.info(
            f"Closed trade for {symbol}: exit={exit_price}, pnl={pnl:.4f}, reason={exit_reason}"
        )

    async def update_trailing_stop(self, symbol: str, new_stop: float):
        """Update the trailing stop price for the open trade on *symbol*."""
        await self._db.execute(
            """
            UPDATE trades
               SET trailing_stop_price = ?
             WHERE symbol = ? AND status = 'open'
            """,
            (new_stop, symbol),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Bot sessions
    # ------------------------------------------------------------------

    async def start_session(self, strategy: str = "", trading_mode: str = "") -> int:
        """Record a new bot session. Returns the session row id."""
        cursor = await self._db.execute(
            """
            INSERT INTO bot_sessions (start_time, strategy, trading_mode)
            VALUES (?, ?, ?)
            """,
            (int(datetime.now().timestamp()), strategy, trading_mode),
        )
        await self._db.commit()
        session_id = cursor.lastrowid
        logger.info(f"Bot session #{session_id} started")
        return session_id

    async def end_session(
        self,
        session_id: int,
        exit_reason: str = "clean_shutdown",
    ):
        """Mark a session as ended and record its uptime."""
        if self._db is None:
            logger.warning("TradeStore end_session called after database close")
            return

        now_ts = int(datetime.now().timestamp())
        # Fetch start_time to compute duration
        cursor = await self._db.execute(
            "SELECT start_time FROM bot_sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        uptime_seconds = 0.0
        if row and row["start_time"]:
            start_dt = self._parse_timestamp(row["start_time"])
            uptime_seconds = (self._parse_timestamp(now_ts) - start_dt).total_seconds()

        await self._db.execute(
            """
            UPDATE bot_sessions
               SET end_time       = ?,
                   uptime_seconds = ?,
                   exit_reason    = ?
             WHERE id = ?
            """,
            (now_ts, uptime_seconds, exit_reason, session_id),
        )
        await self._db.commit()
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        logger.info(
            f"Bot session #{session_id} ended: "
            f"{int(hours)}h {int(minutes)}m {int(secs)}s ({exit_reason})"
        )

    async def get_total_uptime(self) -> float:
        """Return cumulative uptime in seconds across all sessions."""
        cursor = await self._db.execute(
            "SELECT COALESCE(SUM(uptime_seconds), 0) as total FROM bot_sessions"
        )
        row = await cursor.fetchone()
        return float(row["total"]) if row else 0.0

    async def get_session_count(self) -> int:
        """Return the total number of recorded sessions (restarts)."""
        cursor = await self._db.execute("SELECT COUNT(*) as cnt FROM bot_sessions")
        row = await cursor.fetchone()
        return int(row["cnt"]) if row else 0

    async def get_aggregate_stats(self) -> Dict:
        """Return aggregate PnL and win/loss counts from all closed trades."""
        query = """
            SELECT
                COUNT(*) as total_count,
                SUM(pnl) as total_pnl,
                COUNT(CASE WHEN pnl > 0 THEN 1 END) as win_count,
                COUNT(CASE WHEN pnl <= 0 THEN 1 END) as loss_count,
                SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gross_profit,
                SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END) as gross_loss,
                AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_win,
                AVG(CASE WHEN pnl < 0 THEN pnl END) as avg_loss
            FROM trades
            WHERE status = 'closed'
        """
        cursor = await self._db.execute(query)
        row = await cursor.fetchone()
        return {
            "total_trades": row["total_count"] or 0,
            "total_pnl": row["total_pnl"] or 0.0,
            "win_count": row["win_count"] or 0,
            "loss_count": row["loss_count"] or 0,
            "gross_profit": row["gross_profit"] or 0.0,
            "gross_loss": abs(row["gross_loss"] or 0.0),
            "avg_win": row["avg_win"] or 0.0,
            "avg_loss": abs(row["avg_loss"] or 0.0),
        }
