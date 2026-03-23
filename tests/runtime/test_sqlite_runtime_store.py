from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from domain.events import (
    EventMeta,
    FillPayload,
    KlinePayload,
    EventType,
    RuntimeEvent,
)
from adapters.sqlite.runtime_store import SQLiteRuntimeStore
from services.recovery_service import RecoveryIssue, RecoveryReport


def build_event(
    event_id: str = "evt-1",
    event_type: EventType = "execution.fill",
) -> RuntimeEvent:
    payload = FillPayload(
        client_order_id="intent-1",
        exchange_order_id="order-1",
        symbol="BTC/USDT",
        side="buy",
        fill_qty=0.25,
        fill_price=65012.5,
        cumulative_qty=0.25,
        cumulative_quote_qty=16253.125,
        fee_cost=0.05,
        fee_currency="USDT",
        is_maker=False,
        reduce_only=False,
    )
    if event_type == "market.kline":
        payload = KlinePayload(
            interval="1m",
            open_time=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
            close_time=datetime(2026, 3, 22, 9, 1, tzinfo=timezone.utc),
            open=65000.0,
            high=65050.0,
            low=64980.0,
            close=65012.5,
            volume=42.0,
            is_closed=True,
        )
    return RuntimeEvent(
        meta=EventMeta(
            event_id=event_id,
            event_type=event_type,
            source="test",
            symbol="BTC/USDT",
            ts_exchange=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
            ts_received=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
        ),
        payload=payload,
    )


def build_report() -> RecoveryReport:
    return RecoveryReport(
        processed_trades=1,
        restored_symbols=("BTC/USDT",),
        protected_symbols=("BTC/USDT",),
        orphaned_symbols=(),
        unprotected_symbols=("ETH/USDT",),
        reconcile_error_symbols=("ETH/USDT",),
        skipped_symbols=(),
        issues=(
            RecoveryIssue(
                symbol="ETH/USDT",
                kind="unprotected_position",
                reason="native protection missing after restart",
            ),
        ),
    )


def test_sqlite_runtime_store_records_runtime_events(tmp_path) -> None:
    db_path = tmp_path / "runtime.db"
    store = SQLiteRuntimeStore(db_path=str(db_path))

    async def run() -> None:
        await store.initialize()
        await store.record_event(build_event())
        await store.record_event(
            build_event(event_id="evt-2", event_type="market.kline")
        )
        rows = await store.fetch_runtime_events()
        await store.close()
        assert len(rows) == 2
        assert rows[0]["event_id"] == "evt-1"
        assert rows[0]["event_type"] == "execution.fill"
        assert rows[0]["payload_type"] == "FillPayload"
        payload = json.loads(rows[0]["payload_json"])
        assert payload["symbol"] == "BTC/USDT"
        assert payload["fill_qty"] == 0.25
        assert rows[1]["payload_type"] == "KlinePayload"
        assert json.loads(rows[1]["payload_json"])["interval"] == "1m"

    asyncio.run(run())


def test_sqlite_runtime_store_records_recovery_reports(tmp_path) -> None:
    db_path = tmp_path / "runtime.db"
    store = SQLiteRuntimeStore(db_path=str(db_path))

    async def run() -> None:
        await store.initialize()
        await store.record_recovery_report(build_report())
        rows = await store.fetch_recovery_reports()
        await store.close()
        assert len(rows) == 1
        assert rows[0]["processed_trades"] == 1
        assert json.loads(rows[0]["protected_symbols_json"]) == ["BTC/USDT"]
        issues = json.loads(rows[0]["issues_json"])
        assert issues[0]["symbol"] == "ETH/USDT"
        assert issues[0]["kind"] == "unprotected_position"

    asyncio.run(run())


def test_sqlite_runtime_store_requires_initialization(tmp_path) -> None:
    store = SQLiteRuntimeStore(db_path=str(tmp_path / "runtime.db"))

    try:
        asyncio.run(store.record_event(build_event()))
    except RuntimeError as exc:
        assert "has not been initialized" in str(exc)
    else:
        raise AssertionError("store should require initialization")
