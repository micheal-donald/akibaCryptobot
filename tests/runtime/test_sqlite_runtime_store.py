from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from adapters.sqlite.runtime_store import SQLiteRuntimeStore
from domain.events import (
    EventMeta,
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
from services.recovery_service import RecoveryIssue, RecoveryReport


def _meta(
    *, event_id: str, event_type: str, symbol: str | None = "BTC/USDT"
) -> EventMeta:
    ts = datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc)
    return EventMeta(
        event_id=event_id,
        event_type=event_type,  # type: ignore[arg-type]
        source="test",
        symbol=symbol,
        ts_exchange=ts,
        ts_received=ts,
    )


def _build_kline_closed_event() -> RuntimeEvent:
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
        meta=_meta(event_id="evt-kline", event_type="market.kline_closed"),
        payload=payload,
    )


def _build_order_intent_event() -> RuntimeEvent:
    payload = OrderIntentPayload(
        intent_id="intent-1",
        symbol="BTC/USDT",
        side="buy",
        order_type="limit",
        quantity=0.25,
        limit_price=65000.0,
        stop_loss=64000.0,
        take_profit=66000.0,
        reduce_only=False,
        strategy="ensemble",
        reason="test_intent",
    )
    return RuntimeEvent(
        meta=_meta(event_id="evt-intent", event_type="signal.order_intent"),
        payload=payload,
    )


def _build_risk_block_event() -> RuntimeEvent:
    payload = RiskBlockedPayload(
        intent_id="intent-2",
        symbol="ETH/USDT",
        reason="max_open_positions",
    )
    return RuntimeEvent(
        meta=_meta(event_id="evt-risk", event_type="risk.blocked", symbol="ETH/USDT"),
        payload=payload,
    )


def _build_order_submitted_event() -> RuntimeEvent:
    payload = OrderSubmittedPayload(
        client_order_id="intent-1",
        exchange_order_id="order-1",
        symbol="BTC/USDT",
        side="buy",
        order_type="limit",
        requested_qty=0.25,
        requested_price=65000.0,
        reduce_only=False,
    )
    return RuntimeEvent(
        meta=_meta(event_id="evt-order-sub", event_type="execution.order_submitted"),
        payload=payload,
    )


def _build_order_update_event() -> RuntimeEvent:
    payload = OrderUpdatePayload(
        client_order_id="intent-1",
        exchange_order_id="order-1",
        symbol="BTC/USDT",
        status="partially_filled",
        filled_qty=0.1,
        remaining_qty=0.15,
        avg_fill_price=65010.0,
        reduce_only=False,
        raw_status="PARTIALLY_FILLED",
    )
    return RuntimeEvent(
        meta=_meta(event_id="evt-order-upd", event_type="execution.order_update"),
        payload=payload,
    )


def _build_fill_event() -> RuntimeEvent:
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
    return RuntimeEvent(
        meta=_meta(event_id="evt-fill", event_type="execution.fill"),
        payload=payload,
    )


def _build_order_rejected_event() -> RuntimeEvent:
    payload = OrderRejectedPayload(
        intent_id="intent-1",
        symbol="BTC/USDT",
        reason="exchange_reject",
    )
    return RuntimeEvent(
        meta=_meta(event_id="evt-reject", event_type="execution.order_rejected"),
        payload=payload,
    )


def _build_position_opened_event() -> RuntimeEvent:
    payload = PositionOpenedPayload(
        position_id="pos-1",
        symbol="BTC/USDT",
        side="buy",
        quantity=0.25,
        avg_entry_price=65012.5,
        entry_order_ids=("order-1",),
    )
    return RuntimeEvent(
        meta=_meta(event_id="evt-pos-open", event_type="position.opened"),
        payload=payload,
    )


def _build_position_protected_event() -> RuntimeEvent:
    payload = ProtectionSubmittedPayload(
        client_order_id="pos-1",
        symbol="BTC/USDT",
        status="native_submitted",
        mode="native",
        requested_qty=0.25,
        submitted_order_ids=("sl-1", "tp-1"),
        reason=None,
        stop_loss=64000.0,
        take_profit=66000.0,
        trailing_stop=None,
        attempted_at=datetime(2026, 3, 22, 9, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 3, 22, 9, 1, tzinfo=timezone.utc),
    )
    return RuntimeEvent(
        meta=_meta(
            event_id="evt-pos-protect",
            event_type="position.protective_submitted",
        ),
        payload=payload,
    )


def _build_position_closed_event() -> RuntimeEvent:
    payload = PositionClosedPayload(
        position_id="pos-1",
        symbol="BTC/USDT",
        side="buy",
        quantity=0.25,
        avg_entry_price=65012.5,
        avg_exit_price=65200.0,
        close_reason="take_profit",
        pnl=46.875,
        fee_cost=0.1,
    )
    return RuntimeEvent(
        meta=_meta(event_id="evt-pos-close", event_type="position.closed"),
        payload=payload,
    )


def _build_shutdown_event() -> RuntimeEvent:
    payload = {"reason": "test_shutdown"}
    return RuntimeEvent(
        meta=_meta(event_id="evt-shutdown", event_type="runtime.shutdown", symbol=None),
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


def test_sqlite_runtime_store_materializes_runtime_events(tmp_path) -> None:
    db_path = tmp_path / "runtime.db"
    store = SQLiteRuntimeStore(db_path=str(db_path))

    async def run() -> None:
        await store.initialize()
        for event in (
            _build_kline_closed_event(),
            _build_order_intent_event(),
            _build_risk_block_event(),
            _build_order_submitted_event(),
            _build_order_update_event(),
            _build_fill_event(),
            _build_order_rejected_event(),
            _build_position_opened_event(),
            _build_position_protected_event(),
            _build_position_closed_event(),
            _build_shutdown_event(),
        ):
            await store.record_event(event)

        generic_rows = await store.fetch_runtime_events()
        candles = await store.fetch_runtime_candles()
        intents = await store.fetch_runtime_order_intents()
        risk_blocks = await store.fetch_runtime_risk_blocks()
        orders = await store.fetch_runtime_orders()
        fills = await store.fetch_runtime_fills()
        positions = await store.fetch_runtime_positions()
        await store.close()

        assert len(generic_rows) == 1
        assert generic_rows[0]["event_type"] == "runtime.shutdown"
        assert len(candles) == 1
        assert candles[0]["interval"] == "1m"
        assert len(intents) == 1
        assert intents[0]["intent_id"] == "intent-1"
        assert len(risk_blocks) == 1
        assert risk_blocks[0]["reason"] == "max_open_positions"
        assert len(orders) == 1
        assert orders[0]["client_order_id"] == "intent-1"
        assert orders[0]["status"] == "rejected"
        assert len(fills) == 1
        assert fills[0]["fill_qty"] == 0.25
        assert len(positions) == 1
        assert positions[0]["position_id"] == "pos-1"
        assert positions[0]["status"] == "closed"
        assert json.loads(positions[0]["protective_order_ids_json"]) == ["sl-1", "tp-1"]

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


def test_sqlite_runtime_store_batch_rollback_discards_uncommitted_rows(
    tmp_path,
) -> None:
    db_path = tmp_path / "runtime.db"
    store = SQLiteRuntimeStore(db_path=str(db_path))

    async def run() -> None:
        await store.initialize()
        await store.begin_batch()
        await store.record_event(_build_kline_closed_event())
        await store.record_event(_build_shutdown_event())
        await store.rollback_batch()
        candles = await store.fetch_runtime_candles()
        generic_rows = await store.fetch_runtime_events()
        await store.close()
        assert candles == []
        assert generic_rows == []

    asyncio.run(run())


def test_sqlite_runtime_store_requires_initialization(tmp_path) -> None:
    store = SQLiteRuntimeStore(db_path=str(tmp_path / "runtime.db"))

    try:
        asyncio.run(store.record_event(_build_fill_event()))
    except RuntimeError as exc:
        assert "has not been initialized" in str(exc)
    else:
        raise AssertionError("store should require initialization")
