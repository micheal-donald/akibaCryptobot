"""SQLite-backed runtime store for warm-path event materialization."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import aiosqlite

from domain.events import (
    FillPayload,
    KlinePayload,
    OrderIntentPayload,
    OrderRejectedPayload,
    OrderSubmittedPayload,
    OrderUpdatePayload,
    PositionClosedPayload,
    PositionOpenedPayload,
    ProtectionSubmittedPayload,
    RiskBlockedPayload,
    RuntimeEvent,
)
from ports.persistence import RuntimeStore

if TYPE_CHECKING:
    from services.recovery_service import RecoveryReport


GENERIC_RUNTIME_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "runtime.shutdown",
    }
)

EPHEMERAL_RUNTIME_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "market.tick",
        "market.kline",
        "market.spread",
        "execution.balance_update",
        "market.kline_closed",
        "microstructure.decision",
        "signal.suppressed",
        "timer.housekeeping",
    }
)

MATERIALIZED_RUNTIME_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "signal.order_intent",
        "risk.blocked",
        "execution.order_submitted",
        "execution.order_update",
        "execution.order_rejected",
        "execution.fill",
        "position.opened",
        "position.protective_submitted",
        "position.closed",
    }
)


CREATE_RUNTIME_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS runtime_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT    NOT NULL UNIQUE,
    event_type      TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    symbol          TEXT,
    ts_exchange     TEXT,
    ts_received     TEXT    NOT NULL,
    payload_type    TEXT    NOT NULL,
    payload_json    TEXT    NOT NULL,
    persisted_at    TEXT    NOT NULL
)
"""

CREATE_RUNTIME_CANDLES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS runtime_candles (
    event_id         TEXT PRIMARY KEY,
    symbol           TEXT NOT NULL,
    interval         TEXT NOT NULL,
    open_time        TEXT NOT NULL,
    close_time       TEXT NOT NULL,
    open             REAL NOT NULL,
    high             REAL NOT NULL,
    low              REAL NOT NULL,
    close            REAL NOT NULL,
    volume           REAL NOT NULL,
    source           TEXT NOT NULL,
    ts_exchange      TEXT,
    ts_received      TEXT NOT NULL,
    persisted_at     TEXT NOT NULL,
    UNIQUE(symbol, interval, close_time)
)
"""

CREATE_RUNTIME_ORDER_INTENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS runtime_order_intents (
    intent_id        TEXT PRIMARY KEY,
    event_id         TEXT NOT NULL UNIQUE,
    symbol           TEXT NOT NULL,
    side             TEXT NOT NULL,
    order_type       TEXT NOT NULL,
    quantity         REAL NOT NULL,
    limit_price      REAL,
    stop_loss        REAL,
    take_profit      REAL,
    reduce_only      INTEGER NOT NULL,
    strategy         TEXT,
    reason           TEXT,
    ts_received      TEXT NOT NULL,
    persisted_at     TEXT NOT NULL
)
"""

CREATE_RUNTIME_RISK_BLOCKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS runtime_risk_blocks (
    event_id         TEXT PRIMARY KEY,
    intent_id        TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    reason           TEXT NOT NULL,
    ts_received      TEXT NOT NULL,
    persisted_at     TEXT NOT NULL
)
"""

CREATE_RUNTIME_ORDERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS runtime_orders (
    client_order_id   TEXT PRIMARY KEY,
    exchange_order_id TEXT,
    symbol            TEXT NOT NULL,
    side              TEXT,
    order_type        TEXT,
    requested_qty     REAL NOT NULL DEFAULT 0.0,
    requested_price   REAL,
    reduce_only       INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL,
    filled_qty        REAL NOT NULL DEFAULT 0.0,
    remaining_qty     REAL,
    avg_fill_price    REAL,
    rejection_reason  TEXT,
    submitted_event_id TEXT,
    last_event_id     TEXT NOT NULL,
    submitted_at      TEXT,
    updated_at        TEXT NOT NULL,
    persisted_at      TEXT NOT NULL
)
"""

CREATE_RUNTIME_FILLS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS runtime_fills (
    event_id            TEXT PRIMARY KEY,
    client_order_id     TEXT,
    exchange_order_id   TEXT,
    symbol              TEXT NOT NULL,
    side                TEXT NOT NULL,
    fill_qty            REAL NOT NULL,
    fill_price          REAL NOT NULL,
    cumulative_qty      REAL NOT NULL,
    cumulative_quote_qty REAL,
    fee_cost            REAL,
    fee_currency        TEXT,
    is_maker            INTEGER,
    reduce_only         INTEGER NOT NULL,
    ts_exchange         TEXT,
    ts_received         TEXT NOT NULL,
    persisted_at        TEXT NOT NULL
)
"""

CREATE_RUNTIME_POSITIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS runtime_positions (
    position_id              TEXT PRIMARY KEY,
    symbol                   TEXT NOT NULL,
    side                     TEXT NOT NULL,
    requested_qty            REAL NOT NULL DEFAULT 0.0,
    open_qty                 REAL NOT NULL DEFAULT 0.0,
    avg_entry_price          REAL,
    avg_exit_price           REAL,
    status                   TEXT NOT NULL,
    stop_loss                REAL,
    take_profit              REAL,
    trailing_stop            REAL,
    close_reason             TEXT,
    pnl                      REAL,
    fee_cost                 REAL,
    entry_order_ids_json     TEXT NOT NULL,
    protective_order_ids_json TEXT NOT NULL,
    opened_at                TEXT,
    closed_at                TEXT,
    updated_at               TEXT NOT NULL,
    last_event_id            TEXT NOT NULL,
    persisted_at             TEXT NOT NULL
)
"""

CREATE_RUNTIME_RECOVERY_REPORTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS runtime_recovery_reports (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    processed_trades            INTEGER NOT NULL,
    restored_symbols_json       TEXT    NOT NULL,
    protected_symbols_json      TEXT    NOT NULL,
    orphaned_symbols_json       TEXT    NOT NULL,
    unprotected_symbols_json    TEXT    NOT NULL,
    reconcile_error_symbols_json TEXT   NOT NULL,
    skipped_symbols_json        TEXT    NOT NULL,
    issues_json                 TEXT    NOT NULL,
    persisted_at                TEXT    NOT NULL
)
"""


class SQLiteRuntimeStore(RuntimeStore):
    """Persist representative runtime events into SQLite."""

    def __init__(self, db_path: str = "data/runtime.db") -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._batch_depth: int = 0

    async def initialize(self) -> None:
        """Open the database connection and create runtime tables."""

        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        await self._db.execute(CREATE_RUNTIME_EVENTS_TABLE_SQL)
        await self._db.execute(CREATE_RUNTIME_CANDLES_TABLE_SQL)
        await self._db.execute(CREATE_RUNTIME_ORDER_INTENTS_TABLE_SQL)
        await self._db.execute(CREATE_RUNTIME_RISK_BLOCKS_TABLE_SQL)
        await self._db.execute(CREATE_RUNTIME_ORDERS_TABLE_SQL)
        await self._db.execute(CREATE_RUNTIME_FILLS_TABLE_SQL)
        await self._db.execute(CREATE_RUNTIME_POSITIONS_TABLE_SQL)
        await self._db.execute(CREATE_RUNTIME_RECOVERY_REPORTS_TABLE_SQL)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_events_type_received "
            "ON runtime_events(event_type, ts_received)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_candles_symbol_interval_close "
            "ON runtime_candles(symbol, interval, close_time DESC)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_risk_blocks_intent "
            "ON runtime_risk_blocks(intent_id, ts_received DESC)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_orders_exchange "
            "ON runtime_orders(exchange_order_id)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_orders_symbol_status "
            "ON runtime_orders(symbol, status, updated_at DESC)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_fills_symbol_received "
            "ON runtime_fills(symbol, ts_received DESC)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_fills_client_order "
            "ON runtime_fills(client_order_id, ts_received DESC)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_positions_symbol_status "
            "ON runtime_positions(symbol, status, updated_at DESC)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_recovery_reports_persisted "
            "ON runtime_recovery_reports(persisted_at)"
        )
        await self._db.commit()

    async def close(self) -> None:
        """Close the underlying SQLite connection."""

        if self._db is None:
            return
        if self._batch_depth > 0:
            await self._db.rollback()
            self._batch_depth = 0
        await self._db.close()
        self._db = None

    async def begin_batch(self) -> None:
        """Start a transactional batch for warm-path persistence."""

        db = self._require_db()
        if self._batch_depth == 0:
            await db.execute("BEGIN")
        self._batch_depth += 1

    async def commit_batch(self) -> None:
        """Commit the active transactional batch."""

        db = self._require_db()
        if self._batch_depth <= 0:
            return
        self._batch_depth -= 1
        if self._batch_depth == 0:
            await db.commit()

    async def rollback_batch(self) -> None:
        """Rollback the active transactional batch."""

        db = self._require_db()
        if self._batch_depth <= 0:
            return
        await db.rollback()
        self._batch_depth = 0

    async def record_event(self, event: RuntimeEvent) -> None:
        """Persist a normalized runtime event into typed runtime tables."""

        db = self._require_db()
        event_type = event.meta.event_type
        now_text = self._serialize_datetime(self._now())
        if now_text is None:
            now_text = ""

        if event_type in EPHEMERAL_RUNTIME_EVENT_TYPES:
            await self._commit_if_autocommit(db)
            return

        if event_type == "signal.order_intent":
            await self._record_order_intent_event(
                db,
                event=event,
                persisted_at=now_text,
            )
        elif event_type == "risk.blocked":
            await self._record_risk_block_event(
                db,
                event=event,
                persisted_at=now_text,
            )
        elif event_type in {"execution.order_submitted", "execution.order_update"}:
            await self._record_order_state_event(
                db,
                event=event,
                persisted_at=now_text,
            )
        elif event_type == "execution.order_rejected":
            await self._record_order_rejected_event(
                db,
                event=event,
                persisted_at=now_text,
            )
        elif event_type == "execution.fill":
            await self._record_fill_event(
                db,
                event=event,
                persisted_at=now_text,
            )
        elif event_type in {
            "position.opened",
            "position.protective_submitted",
            "position.closed",
        }:
            await self._record_position_event(
                db,
                event=event,
                persisted_at=now_text,
            )
        elif event_type in GENERIC_RUNTIME_EVENT_TYPES:
            await self._record_generic_runtime_event(
                db,
                event=event,
                persisted_at=now_text,
            )
        elif event_type not in MATERIALIZED_RUNTIME_EVENT_TYPES:
            # Unknown or future event types are kept in the generic table so we
            # retain forensic visibility without requiring immediate schema work.
            await self._record_generic_runtime_event(
                db,
                event=event,
                persisted_at=now_text,
            )

        await self._commit_if_autocommit(db)

    async def record_recovery_report(self, report: "RecoveryReport") -> None:
        """Persist a bounded recovery report summary."""

        db = self._require_db()
        await db.execute(
            """
            INSERT INTO runtime_recovery_reports (
                processed_trades,
                restored_symbols_json,
                protected_symbols_json,
                orphaned_symbols_json,
                unprotected_symbols_json,
                reconcile_error_symbols_json,
                skipped_symbols_json,
                issues_json,
                persisted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.processed_trades,
                self._json_dump(report.restored_symbols),
                self._json_dump(report.protected_symbols),
                self._json_dump(report.orphaned_symbols),
                self._json_dump(report.unprotected_symbols),
                self._json_dump(report.reconcile_error_symbols),
                self._json_dump(report.skipped_symbols),
                self._json_dump(report.issues),
                self._serialize_datetime(self._now()),
            ),
        )
        await self._commit_if_autocommit(db)

    async def fetch_runtime_events(self) -> list[dict[str, Any]]:
        """Return generic stored runtime events for tests and diagnostics."""

        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT event_id, event_type, source, symbol, ts_exchange,
                   ts_received, payload_type, payload_json, persisted_at
              FROM runtime_events
             ORDER BY id ASC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_runtime_candles(self) -> list[dict[str, Any]]:
        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT event_id, symbol, interval, open_time, close_time, open, high,
                   low, close, volume, source, ts_exchange, ts_received, persisted_at
              FROM runtime_candles
             ORDER BY close_time ASC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_runtime_order_intents(self) -> list[dict[str, Any]]:
        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT intent_id, event_id, symbol, side, order_type, quantity,
                   limit_price, stop_loss, take_profit, reduce_only, strategy,
                   reason, ts_received, persisted_at
              FROM runtime_order_intents
             ORDER BY ts_received ASC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_runtime_risk_blocks(self) -> list[dict[str, Any]]:
        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT event_id, intent_id, symbol, reason, ts_received, persisted_at
              FROM runtime_risk_blocks
             ORDER BY ts_received ASC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_runtime_orders(self) -> list[dict[str, Any]]:
        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT client_order_id, exchange_order_id, symbol, side, order_type,
                   requested_qty, requested_price, reduce_only, status, filled_qty,
                   remaining_qty, avg_fill_price, rejection_reason,
                   submitted_event_id, last_event_id, submitted_at, updated_at,
                   persisted_at
              FROM runtime_orders
             ORDER BY updated_at ASC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_runtime_fills(self) -> list[dict[str, Any]]:
        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT event_id, client_order_id, exchange_order_id, symbol, side,
                   fill_qty, fill_price, cumulative_qty, cumulative_quote_qty,
                   fee_cost, fee_currency, is_maker, reduce_only, ts_exchange,
                   ts_received, persisted_at
              FROM runtime_fills
             ORDER BY ts_received ASC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_runtime_positions(self) -> list[dict[str, Any]]:
        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT position_id, symbol, side, requested_qty, open_qty,
                   avg_entry_price, avg_exit_price, status, stop_loss, take_profit,
                   trailing_stop, close_reason, pnl, fee_cost, entry_order_ids_json,
                   protective_order_ids_json, opened_at, closed_at, updated_at,
                   last_event_id, persisted_at
              FROM runtime_positions
             ORDER BY updated_at ASC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_recovery_reports(self) -> list[dict[str, Any]]:
        """Return stored recovery report rows for tests and diagnostics."""

        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT processed_trades,
                   restored_symbols_json,
                   protected_symbols_json,
                   orphaned_symbols_json,
                   unprotected_symbols_json,
                   reconcile_error_symbols_json,
                   skipped_symbols_json,
                   issues_json,
                   persisted_at
              FROM runtime_recovery_reports
             ORDER BY id ASC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def _record_generic_runtime_event(
        self,
        db: aiosqlite.Connection,
        *,
        event: RuntimeEvent,
        persisted_at: str,
    ) -> None:
        meta = event.meta
        payload = self._normalize_value(event.payload)
        await db.execute(
            """
            INSERT OR REPLACE INTO runtime_events (
                event_id, event_type, source, symbol, ts_exchange,
                ts_received, payload_type, payload_json, persisted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                meta.event_id,
                meta.event_type,
                meta.source,
                meta.symbol,
                self._serialize_datetime(meta.ts_exchange),
                self._serialize_datetime(meta.ts_received),
                type(event.payload).__name__,
                json.dumps(payload, separators=(",", ":"), sort_keys=True),
                persisted_at,
            ),
        )

    async def _record_kline_closed_event(
        self,
        db: aiosqlite.Connection,
        *,
        event: RuntimeEvent,
        persisted_at: str,
    ) -> None:
        payload = event.payload
        if not isinstance(payload, KlinePayload):
            return
        await db.execute(
            """
            INSERT OR REPLACE INTO runtime_candles (
                event_id, symbol, interval, open_time, close_time, open, high, low,
                close, volume, source, ts_exchange, ts_received, persisted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.meta.event_id,
                event.meta.symbol or "",
                payload.interval,
                self._serialize_datetime(payload.open_time),
                self._serialize_datetime(payload.close_time),
                payload.open,
                payload.high,
                payload.low,
                payload.close,
                payload.volume,
                event.meta.source,
                self._serialize_datetime(event.meta.ts_exchange),
                self._serialize_datetime(event.meta.ts_received),
                persisted_at,
            ),
        )

    async def _record_order_intent_event(
        self,
        db: aiosqlite.Connection,
        *,
        event: RuntimeEvent,
        persisted_at: str,
    ) -> None:
        payload = event.payload
        if not isinstance(payload, OrderIntentPayload):
            return
        await db.execute(
            """
            INSERT OR REPLACE INTO runtime_order_intents (
                intent_id, event_id, symbol, side, order_type, quantity, limit_price,
                stop_loss, take_profit, reduce_only, strategy, reason, ts_received,
                persisted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.intent_id,
                event.meta.event_id,
                payload.symbol,
                payload.side,
                payload.order_type,
                payload.quantity,
                payload.limit_price,
                payload.stop_loss,
                payload.take_profit,
                self._bool_to_int(payload.reduce_only),
                payload.strategy,
                payload.reason,
                self._serialize_datetime(event.meta.ts_received),
                persisted_at,
            ),
        )

    async def _record_risk_block_event(
        self,
        db: aiosqlite.Connection,
        *,
        event: RuntimeEvent,
        persisted_at: str,
    ) -> None:
        payload = event.payload
        if not isinstance(payload, RiskBlockedPayload):
            return
        await db.execute(
            """
            INSERT OR REPLACE INTO runtime_risk_blocks (
                event_id, intent_id, symbol, reason, ts_received, persisted_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.meta.event_id,
                payload.intent_id,
                payload.symbol,
                payload.reason,
                self._serialize_datetime(event.meta.ts_received),
                persisted_at,
            ),
        )

    async def _record_order_state_event(
        self,
        db: aiosqlite.Connection,
        *,
        event: RuntimeEvent,
        persisted_at: str,
    ) -> None:
        payload = event.payload
        if isinstance(payload, OrderSubmittedPayload):
            client_order_id = payload.client_order_id
            await db.execute(
                """
                INSERT INTO runtime_orders (
                    client_order_id, exchange_order_id, symbol, side, order_type,
                    requested_qty, requested_price, reduce_only, status, filled_qty,
                    remaining_qty, avg_fill_price, rejection_reason,
                    submitted_event_id, last_event_id, submitted_at, updated_at,
                    persisted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_order_id) DO UPDATE SET
                    exchange_order_id=COALESCE(excluded.exchange_order_id, runtime_orders.exchange_order_id),
                    symbol=excluded.symbol,
                    side=COALESCE(excluded.side, runtime_orders.side),
                    order_type=COALESCE(excluded.order_type, runtime_orders.order_type),
                    requested_qty=MAX(runtime_orders.requested_qty, excluded.requested_qty),
                    requested_price=COALESCE(excluded.requested_price, runtime_orders.requested_price),
                    reduce_only=excluded.reduce_only,
                    status=excluded.status,
                    submitted_event_id=COALESCE(runtime_orders.submitted_event_id, excluded.submitted_event_id),
                    last_event_id=excluded.last_event_id,
                    submitted_at=COALESCE(runtime_orders.submitted_at, excluded.submitted_at),
                    updated_at=excluded.updated_at,
                    persisted_at=excluded.persisted_at
                """,
                (
                    client_order_id,
                    payload.exchange_order_id,
                    payload.symbol,
                    payload.side,
                    payload.order_type,
                    payload.requested_qty,
                    payload.requested_price,
                    self._bool_to_int(payload.reduce_only),
                    "submitted",
                    0.0,
                    payload.requested_qty,
                    None,
                    None,
                    event.meta.event_id,
                    event.meta.event_id,
                    self._serialize_datetime(event.meta.ts_received),
                    self._serialize_datetime(event.meta.ts_received),
                    persisted_at,
                ),
            )
            return

        if not isinstance(payload, OrderUpdatePayload):
            return
        client_order_id = self._order_row_id_from_update(payload, event.meta.event_id)
        await db.execute(
            """
            INSERT INTO runtime_orders (
                client_order_id, exchange_order_id, symbol, side, order_type,
                requested_qty, requested_price, reduce_only, status, filled_qty,
                remaining_qty, avg_fill_price, rejection_reason, submitted_event_id,
                last_event_id, submitted_at, updated_at, persisted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                exchange_order_id=COALESCE(excluded.exchange_order_id, runtime_orders.exchange_order_id),
                symbol=excluded.symbol,
                reduce_only=excluded.reduce_only,
                status=excluded.status,
                filled_qty=MAX(runtime_orders.filled_qty, excluded.filled_qty),
                remaining_qty=COALESCE(excluded.remaining_qty, runtime_orders.remaining_qty),
                avg_fill_price=COALESCE(excluded.avg_fill_price, runtime_orders.avg_fill_price),
                last_event_id=excluded.last_event_id,
                updated_at=excluded.updated_at,
                persisted_at=excluded.persisted_at
            """,
            (
                client_order_id,
                payload.exchange_order_id,
                payload.symbol,
                None,
                None,
                0.0,
                None,
                self._bool_to_int(payload.reduce_only),
                payload.status,
                payload.filled_qty,
                payload.remaining_qty,
                payload.avg_fill_price,
                None,
                None,
                event.meta.event_id,
                None,
                self._serialize_datetime(event.meta.ts_received),
                persisted_at,
            ),
        )

    async def _record_order_rejected_event(
        self,
        db: aiosqlite.Connection,
        *,
        event: RuntimeEvent,
        persisted_at: str,
    ) -> None:
        payload = event.payload
        if not isinstance(payload, OrderRejectedPayload):
            return
        client_order_id = payload.intent_id
        await db.execute(
            """
            INSERT INTO runtime_orders (
                client_order_id, exchange_order_id, symbol, side, order_type,
                requested_qty, requested_price, reduce_only, status, filled_qty,
                remaining_qty, avg_fill_price, rejection_reason, submitted_event_id,
                last_event_id, submitted_at, updated_at, persisted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                symbol=excluded.symbol,
                status=excluded.status,
                rejection_reason=excluded.rejection_reason,
                last_event_id=excluded.last_event_id,
                updated_at=excluded.updated_at,
                persisted_at=excluded.persisted_at
            """,
            (
                client_order_id,
                None,
                payload.symbol,
                None,
                None,
                0.0,
                None,
                0,
                "rejected",
                0.0,
                None,
                None,
                payload.reason,
                None,
                event.meta.event_id,
                None,
                self._serialize_datetime(event.meta.ts_received),
                persisted_at,
            ),
        )

    async def _record_fill_event(
        self,
        db: aiosqlite.Connection,
        *,
        event: RuntimeEvent,
        persisted_at: str,
    ) -> None:
        payload = event.payload
        if not isinstance(payload, FillPayload):
            return
        await db.execute(
            """
            INSERT OR REPLACE INTO runtime_fills (
                event_id, client_order_id, exchange_order_id, symbol, side,
                fill_qty, fill_price, cumulative_qty, cumulative_quote_qty, fee_cost,
                fee_currency, is_maker, reduce_only, ts_exchange, ts_received,
                persisted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.meta.event_id,
                payload.client_order_id,
                payload.exchange_order_id,
                payload.symbol,
                payload.side,
                payload.fill_qty,
                payload.fill_price,
                payload.cumulative_qty,
                payload.cumulative_quote_qty,
                payload.fee_cost,
                payload.fee_currency,
                (
                    None
                    if payload.is_maker is None
                    else self._bool_to_int(payload.is_maker)
                ),
                self._bool_to_int(payload.reduce_only),
                self._serialize_datetime(event.meta.ts_exchange),
                self._serialize_datetime(event.meta.ts_received),
                persisted_at,
            ),
        )
        client_order_id = (
            payload.client_order_id
            or self._order_row_id_from_exchange_id(
                payload.exchange_order_id,
                event.meta.event_id,
            )
        )
        status = (
            "filled"
            if payload.cumulative_qty >= payload.fill_qty
            else "partially_filled"
        )
        await db.execute(
            """
            INSERT INTO runtime_orders (
                client_order_id, exchange_order_id, symbol, side, order_type,
                requested_qty, requested_price, reduce_only, status, filled_qty,
                remaining_qty, avg_fill_price, rejection_reason, submitted_event_id,
                last_event_id, submitted_at, updated_at, persisted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                exchange_order_id=COALESCE(excluded.exchange_order_id, runtime_orders.exchange_order_id),
                symbol=excluded.symbol,
                side=COALESCE(excluded.side, runtime_orders.side),
                reduce_only=excluded.reduce_only,
                status=excluded.status,
                filled_qty=MAX(runtime_orders.filled_qty, excluded.filled_qty),
                avg_fill_price=COALESCE(excluded.avg_fill_price, runtime_orders.avg_fill_price),
                last_event_id=excluded.last_event_id,
                updated_at=excluded.updated_at,
                persisted_at=excluded.persisted_at
            """,
            (
                client_order_id,
                payload.exchange_order_id,
                payload.symbol,
                payload.side,
                None,
                payload.cumulative_qty,
                None,
                self._bool_to_int(payload.reduce_only),
                status,
                payload.cumulative_qty,
                0.0,
                payload.fill_price,
                None,
                None,
                event.meta.event_id,
                None,
                self._serialize_datetime(event.meta.ts_received),
                persisted_at,
            ),
        )

    async def _record_position_event(
        self,
        db: aiosqlite.Connection,
        *,
        event: RuntimeEvent,
        persisted_at: str,
    ) -> None:
        payload = event.payload
        if isinstance(payload, PositionOpenedPayload):
            await db.execute(
                """
                INSERT INTO runtime_positions (
                    position_id, symbol, side, requested_qty, open_qty,
                    avg_entry_price, avg_exit_price, status, stop_loss, take_profit,
                    trailing_stop, close_reason, pnl, fee_cost, entry_order_ids_json,
                    protective_order_ids_json, opened_at, closed_at, updated_at,
                    last_event_id, persisted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(position_id) DO UPDATE SET
                    symbol=excluded.symbol,
                    side=excluded.side,
                    requested_qty=excluded.requested_qty,
                    open_qty=excluded.open_qty,
                    avg_entry_price=COALESCE(excluded.avg_entry_price, runtime_positions.avg_entry_price),
                    status=excluded.status,
                    entry_order_ids_json=excluded.entry_order_ids_json,
                    opened_at=COALESCE(runtime_positions.opened_at, excluded.opened_at),
                    updated_at=excluded.updated_at,
                    last_event_id=excluded.last_event_id,
                    persisted_at=excluded.persisted_at
                """,
                (
                    payload.position_id,
                    payload.symbol,
                    payload.side,
                    payload.quantity,
                    payload.quantity,
                    payload.avg_entry_price,
                    None,
                    "open",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    self._json_dump(payload.entry_order_ids),
                    self._json_dump(()),
                    self._serialize_datetime(event.meta.ts_received),
                    None,
                    self._serialize_datetime(event.meta.ts_received),
                    event.meta.event_id,
                    persisted_at,
                ),
            )
            return

        if isinstance(payload, ProtectionSubmittedPayload):
            await db.execute(
                """
                INSERT INTO runtime_positions (
                    position_id, symbol, side, requested_qty, open_qty,
                    avg_entry_price, avg_exit_price, status, stop_loss, take_profit,
                    trailing_stop, close_reason, pnl, fee_cost, entry_order_ids_json,
                    protective_order_ids_json, opened_at, closed_at, updated_at,
                    last_event_id, persisted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(position_id) DO UPDATE SET
                    symbol=excluded.symbol,
                    status=excluded.status,
                    stop_loss=COALESCE(excluded.stop_loss, runtime_positions.stop_loss),
                    take_profit=COALESCE(excluded.take_profit, runtime_positions.take_profit),
                    trailing_stop=COALESCE(excluded.trailing_stop, runtime_positions.trailing_stop),
                    protective_order_ids_json=excluded.protective_order_ids_json,
                    updated_at=excluded.updated_at,
                    last_event_id=excluded.last_event_id,
                    persisted_at=excluded.persisted_at
                """,
                (
                    payload.client_order_id,
                    payload.symbol,
                    "unknown",
                    payload.requested_qty,
                    payload.requested_qty,
                    None,
                    None,
                    "protected" if payload.status == "native_submitted" else "degraded",
                    payload.stop_loss,
                    payload.take_profit,
                    payload.trailing_stop,
                    payload.reason,
                    None,
                    None,
                    self._json_dump((payload.client_order_id,)),
                    self._json_dump(payload.submitted_order_ids),
                    self._serialize_datetime(payload.attempted_at),
                    None,
                    self._serialize_datetime(
                        payload.updated_at or event.meta.ts_received
                    ),
                    event.meta.event_id,
                    persisted_at,
                ),
            )
            return

        if not isinstance(payload, PositionClosedPayload):
            return
        await db.execute(
            """
            INSERT INTO runtime_positions (
                position_id, symbol, side, requested_qty, open_qty,
                avg_entry_price, avg_exit_price, status, stop_loss, take_profit,
                trailing_stop, close_reason, pnl, fee_cost, entry_order_ids_json,
                protective_order_ids_json, opened_at, closed_at, updated_at,
                last_event_id, persisted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(position_id) DO UPDATE SET
                symbol=excluded.symbol,
                side=excluded.side,
                requested_qty=excluded.requested_qty,
                open_qty=excluded.open_qty,
                avg_entry_price=COALESCE(excluded.avg_entry_price, runtime_positions.avg_entry_price),
                avg_exit_price=COALESCE(excluded.avg_exit_price, runtime_positions.avg_exit_price),
                status=excluded.status,
                close_reason=COALESCE(excluded.close_reason, runtime_positions.close_reason),
                pnl=COALESCE(excluded.pnl, runtime_positions.pnl),
                fee_cost=COALESCE(excluded.fee_cost, runtime_positions.fee_cost),
                closed_at=COALESCE(excluded.closed_at, runtime_positions.closed_at),
                updated_at=excluded.updated_at,
                last_event_id=excluded.last_event_id,
                persisted_at=excluded.persisted_at
            """,
            (
                payload.position_id,
                payload.symbol,
                payload.side,
                payload.quantity,
                0.0,
                payload.avg_entry_price,
                payload.avg_exit_price,
                "closed",
                None,
                None,
                None,
                payload.close_reason,
                payload.pnl,
                payload.fee_cost,
                self._json_dump(()),
                self._json_dump(()),
                None,
                self._serialize_datetime(event.meta.ts_received),
                self._serialize_datetime(event.meta.ts_received),
                event.meta.event_id,
                persisted_at,
            ),
        )

    @staticmethod
    def _order_row_id_from_exchange_id(
        exchange_order_id: str | None,
        event_id: str,
    ) -> str:
        if exchange_order_id:
            return f"exchange:{exchange_order_id}"
        return f"event:{event_id}"

    @classmethod
    def _order_row_id_from_update(
        cls,
        payload: OrderUpdatePayload,
        event_id: str,
    ) -> str:
        if payload.client_order_id:
            return payload.client_order_id
        return cls._order_row_id_from_exchange_id(payload.exchange_order_id, event_id)

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SQLiteRuntimeStore has not been initialized")
        return self._db

    async def _commit_if_autocommit(self, db: aiosqlite.Connection) -> None:
        if self._batch_depth == 0:
            await db.commit()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _serialize_datetime(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _bool_to_int(value: bool) -> int:
        return 1 if value else 0

    def _json_dump(self, value: Any) -> str:
        return json.dumps(
            self._normalize_value(value),
            separators=(",", ":"),
            sort_keys=True,
        )

    def _normalize_value(self, value: Any) -> Any:
        if is_dataclass(value):
            return {
                key: self._normalize_value(item) for key, item in asdict(value).items()
            }
        if isinstance(value, datetime):
            return self._serialize_datetime(value)
        if isinstance(value, tuple):
            return [self._normalize_value(item) for item in value]
        if isinstance(value, list):
            return [self._normalize_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): self._normalize_value(item) for key, item in value.items()
            }
        return value
