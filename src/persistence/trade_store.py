"""
Async SQLite-backed trade store.

Replaces the in-memory ``self.active_trades = {}`` dictionary so that
open positions survive bot restarts and crashes.
"""

import os
import logging
from datetime import datetime
from typing import Dict, List, Optional

import aiosqlite

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
    timestamp       TEXT    NOT NULL,
    order_id        TEXT,
    status          TEXT    NOT NULL DEFAULT 'open',
    exit_price      REAL,
    exit_timestamp  TEXT,
    exit_reason     TEXT,
    pnl             REAL,
    fees            REAL    DEFAULT 0.0,
    trailing_stop_price REAL
)
"""

CREATE_SESSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS bot_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time      TEXT    NOT NULL,
    end_time        TEXT,
    uptime_seconds  REAL,
    exit_reason     TEXT    DEFAULT 'unknown',
    strategy        TEXT,
    trading_mode    TEXT
)
"""


class TradeStore:
    """Thin async wrapper around an SQLite database for trade persistence."""

    def __init__(self, db_path: str = "data/trades.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

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
        await self._db.commit()
        logger.info(f"TradeStore initialised → {self.db_path}")

    async def close(self):
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("TradeStore closed")

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
                "timestamp": row["timestamp"],
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
                    "timestamp": row["timestamp"],
                    "exit_timestamp": row["exit_timestamp"],
                    "exit_reason": row["exit_reason"],
                    "pnl": row["pnl"],
                    "fees": row["fees"] or 0.0,
                }
            )
        logger.info(f"Loaded {len(trades)} closed trade(s) from database")
        return trades

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def insert_trade(self, trade: Dict) -> int:
        """Insert a new open trade and return the row id."""
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
                trade.get("timestamp", datetime.now().isoformat()),
                trade.get("order_id"),
                trade.get("entry_fee", trade.get("fees", 0.0)),
                trade.get("trailing_stop_price"),
            ),
        )
        await self._db.commit()
        row_id = cursor.lastrowid
        logger.info(f"Persisted trade #{row_id} for {trade['symbol']}")
        return row_id

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
                datetime.now().isoformat(),
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
            (datetime.now().isoformat(), strategy, trading_mode),
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
        now = datetime.now()
        # Fetch start_time to compute duration
        cursor = await self._db.execute(
            "SELECT start_time FROM bot_sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        uptime_seconds = 0.0
        if row and row["start_time"]:
            start_dt = datetime.fromisoformat(row["start_time"])
            uptime_seconds = (now - start_dt).total_seconds()

        await self._db.execute(
            """
            UPDATE bot_sessions
               SET end_time       = ?,
                   uptime_seconds = ?,
                   exit_reason    = ?
             WHERE id = ?
            """,
            (now.isoformat(), uptime_seconds, exit_reason, session_id),
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
