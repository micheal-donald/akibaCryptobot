"""SQLite-backed runtime store for warm-path event materialization."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import aiosqlite

from domain.events import RuntimeEvent
from ports.persistence import RuntimeStore

if TYPE_CHECKING:
    from services.recovery_service import RecoveryReport


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

    async def initialize(self) -> None:
        """Open the database connection and create runtime tables."""

        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(CREATE_RUNTIME_EVENTS_TABLE_SQL)
        await self._db.execute(CREATE_RUNTIME_RECOVERY_REPORTS_TABLE_SQL)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_events_type_received "
            "ON runtime_events(event_type, ts_received)"
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
        await self._db.close()
        self._db = None

    async def record_event(self, event: RuntimeEvent) -> None:
        """Persist a normalized runtime event."""

        db = self._require_db()
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
                self._serialize_datetime(self._now()),
            ),
        )
        await db.commit()

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
        await db.commit()

    async def fetch_runtime_events(self) -> list[dict[str, Any]]:
        """Return stored runtime events for tests and diagnostics."""

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

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SQLiteRuntimeStore has not been initialized")
        return self._db

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _serialize_datetime(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat()

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
            return value.isoformat()
        if isinstance(value, tuple):
            return [self._normalize_value(item) for item in value]
        if isinstance(value, list):
            return [self._normalize_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): self._normalize_value(item) for key, item in value.items()
            }
        return value
