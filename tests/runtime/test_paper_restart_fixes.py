"""Regression checks for production paper-entry, audit, and restart failures."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.binance.execution_gateway import BinanceExecutionGateway
from domain.events import (
    EventMeta,
    FillPayload,
    OrderIntentPayload,
    OrderSubmittedPayload,
    RuntimeEvent,
)
from runtime.app import RuntimeEventRouter
from services.execution_service import ExecutionService
from services.position_service import PositionService
from services.recovery_service import RestartRecoveryService
from services.signal_service import SignalService
from exchange.spot_user_stream import SpotUserStream


def event(kind, seconds=0, symbol="BTC/USDT", payload=None):
    ts = datetime(2026, 10, 3, tzinfo=timezone.utc) + timedelta(seconds=seconds)
    return RuntimeEvent(
        meta=EventMeta(
            event_id=f"{kind}-{symbol}-{seconds}",
            event_type=kind,
            source="test",
            symbol=symbol,
            ts_exchange=ts,
            ts_received=ts,
        ),
        payload=payload,
    )


@pytest.mark.asyncio
async def test_ephemeral_market_and_display_events_are_not_persisted():
    queued = []
    persistence = MagicMock()
    persistence.enqueue_event.side_effect = lambda e: queued.append(e) or True
    router = RuntimeEventRouter(
        name="test", event_bus=asyncio.Queue(), persistence_service=persistence
    )
    for second in range(121):
        for symbol in ("BTC/USDT", "ETH/USDT"):
            await router._route_event(event("microstructure.decision", second, symbol))
        await router._route_event(event("market.kline_closed", second))
        await router._route_event(event("market.tick", second))
        await router._route_event(event("market.kline", second))
        await router._route_event(event("market.spread", second))
        await router._route_event(event("execution.balance_update", second))
        await router._route_event(event("signal.suppressed", second))
        await router._route_event(event("timer.housekeeping", second))
        await router._route_event(event("execution.fill", second))
    assert not any(
        e.meta.event_type
        in {
            "microstructure.decision",
            "market.kline_closed",
            "market.tick",
            "market.kline",
            "market.spread",
            "execution.balance_update",
            "signal.suppressed",
            "timer.housekeeping",
        }
        for e in queued
    )
    assert sum(e.meta.event_type == "execution.fill" for e in queued) == 121


@pytest.mark.asyncio
async def test_intent_is_audited_even_when_submission_fails():
    output = asyncio.Queue()
    risk = SimpleNamespace(
        submit_order_intent=AsyncMock(side_effect=ValueError("reject"))
    )
    service = SignalService(
        name="test", strategy=MagicMock(), event_bus=output, risk_service=risk
    )
    intent = OrderIntentPayload(
        intent_id="intent",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=0.001,
    )
    with pytest.raises(ValueError, match="reject"):
        await service._emit_intent(
            intent,
            SimpleNamespace(generated_at=datetime.now(timezone.utc)),
            datetime.now(timezone.utc),
        )
    assert output.get_nowait().meta.event_type == "signal.order_intent"


@pytest.mark.asyncio
async def test_local_rejection_does_not_become_submitted_order():
    client = SimpleNamespace(
        place_market_order=AsyncMock(
            return_value={"status": "rejected", "reason": "below minimum notional"}
        )
    )
    gateway = BinanceExecutionGateway(client=client)
    intent = OrderIntentPayload(
        intent_id="intent",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=0.00001,
        limit_price=60000,
    )
    with pytest.raises(ValueError, match="below minimum notional"):
        await gateway.submit_order(intent)


@pytest.mark.asyncio
async def test_restart_recovers_runtime_position_without_legacy_trade():
    gateway = MagicMock()
    gateway.fetch_order = AsyncMock(return_value=None)
    bus = asyncio.Queue()
    execution = ExecutionService(
        name="execution", execution_gateway=gateway, event_bus=bus
    )
    position = PositionService(name="position", event_bus=bus)
    recovery = RestartRecoveryService(
        name="recovery",
        trade_store=SimpleNamespace(load_open_trades=AsyncMock(return_value={})),
        runtime_store=SimpleNamespace(
            fetch_runtime_positions=AsyncMock(
                return_value=[
                    {
                        "position_id": "client-intent",
                        "symbol": "BTC/USDT",
                        "side": "buy",
                        "open_qty": 0.001,
                        "avg_entry_price": 60000,
                        "stop_loss": None,
                        "take_profit": None,
                        "trailing_stop": None,
                        "entry_order_ids_json": '["exchange-42"]',
                        "protective_order_ids_json": "[]",
                        "opened_at": None,
                        "fee_cost": 0.0,
                    }
                ]
            )
        ),
        execution_gateway=gateway,
        execution_service=execution,
        position_service=position,
    )
    report = await recovery.recover()
    assert report.processed_trades == 1
    assert report.restored_symbols == ("BTC/USDT",)
    assert position.get_position_state("client-intent").open_qty == 0.001
    assert execution.get_order_state("client-intent").order_id == "exchange-42"
    gateway.fetch_order.assert_awaited_once_with("BTC/USDT", "exchange-42")


@pytest.mark.asyncio
async def test_rest_fill_is_accounted_once_with_delayed_websocket_copy():
    gateway = SimpleNamespace(
        submit_order=AsyncMock(
            return_value=OrderSubmittedPayload(
                client_order_id="intent",
                exchange_order_id="exchange-1",
                symbol="BTC/USDT",
                side="buy",
                order_type="market",
                requested_qty=0.001,
                filled_qty=0.001,
                avg_fill_price=60000,
            )
        )
    )
    bus = asyncio.Queue()
    service = ExecutionService(name="test", execution_gateway=gateway, event_bus=bus)
    intent = OrderIntentPayload(
        intent_id="intent",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=0.001,
    )
    await service.submit_order_intent(intent)
    await service._handle_fill_event(
        event(
            "execution.fill",
            payload=FillPayload(
                client_order_id="intent",
                exchange_order_id="exchange-1",
                symbol="BTC/USDT",
                side="buy",
                fill_qty=0.001,
                fill_price=60000,
                cumulative_qty=0.001,
            ),
        )
    )
    positions = PositionService(name="position", event_bus=asyncio.Queue())
    fills = []
    while not bus.empty():
        e = bus.get_nowait()
        if e.meta.event_type == "execution.fill":
            fills.append(e)
            await positions.handle_fill_event(e)
    assert len(fills) == 1
    assert positions.get_position_for_symbol("BTC/USDT").open_qty == 0.001


@pytest.mark.asyncio
async def test_spot_exit_fill_preserves_local_reduce_only_intent():
    bus = asyncio.Queue()
    gateway = SimpleNamespace(
        submit_order=AsyncMock(
            return_value=OrderSubmittedPayload(
                client_order_id="exit",
                exchange_order_id="exchange-exit",
                symbol="BTC/USDT",
                side="sell",
                order_type="market",
                requested_qty=0.001,
            )
        )
    )
    service = ExecutionService(name="test", execution_gateway=gateway, event_bus=bus)
    await service.submit_order_intent(
        OrderIntentPayload(
            intent_id="exit",
            symbol="BTC/USDT",
            side="sell",
            order_type="market",
            quantity=0.001,
            reduce_only=True,
        )
    )
    await service._handle_fill_event(
        event(
            "execution.fill",
            payload=FillPayload(
                client_order_id="exit",
                exchange_order_id="exchange-exit",
                symbol="BTC/USDT",
                side="sell",
                fill_qty=0.001,
                fill_price=60100,
                cumulative_qty=0.001,
                reduce_only=False,
            ),
        )
    )
    bus.get_nowait()
    assert bus.get_nowait().payload.reduce_only is True


def test_spot_stream_uses_signed_subscription_without_listenkey():
    import hashlib
    import hmac

    stream = SpotUserStream("test-key", "test-secret", True, AsyncMock())
    request = stream.subscription_request(123456)
    assert request["method"] == "userDataStream.subscribe.signature"
    expected = hmac.new(
        b"test-secret",
        b"apiKey=test-key&recvWindow=10000&timestamp=123456",
        hashlib.sha256,
    ).hexdigest()
    assert request["params"]["signature"] == expected
    assert "testnet" in stream.url


def test_offline_compaction_preserves_trading_records_and_drops_research(tmp_path):
    import sqlite3
    from scripts.compact_runtime_store import compact_database

    path = tmp_path / "runtime.db"
    with sqlite3.connect(path) as seed:
        seed.execute(
            "CREATE TABLE runtime_events (id INTEGER PRIMARY KEY, event_type TEXT)"
        )
        seed.execute(
            "CREATE TABLE runtime_positions (position_id TEXT PRIMARY KEY, open_qty REAL)"
        )
        seed.executemany(
            "INSERT INTO runtime_events (event_type) VALUES (?)",
            [
                ("microstructure.decision",),
                ("signal.suppressed",),
                ("timer.housekeeping",),
                ("market.kline_closed",),
                ("market.tick",),
                ("market.kline",),
                ("runtime.shutdown",),
                ("position.opened",),
            ],
        )
        seed.execute("INSERT INTO runtime_positions VALUES ('intent', 0.001)")
    result = compact_database(path)
    with sqlite3.connect(path) as active:
        assert (
            active.execute("SELECT COUNT(*) FROM runtime_positions").fetchone()[0] == 1
        )
        assert set(
            active.execute("SELECT event_type FROM runtime_events").fetchall()
        ) == {
            ("runtime.shutdown",),
            ("position.opened",),
        }
        assert active.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert result["preserved_rows"]["ephemeral_events_removed"] == 6
    assert not list(tmp_path.glob("runtime.archive-*.db"))


def test_disconnected_user_stream_blocks_entries_and_reports_unhealthy():
    from runtime.projection import build_runtime_projection
    from services.risk_service import RiskService

    execution = SimpleNamespace(
        user_stream_gateway=SimpleNamespace(get_status=lambda: {"connected": False}),
        get_all_order_states=lambda: (),
    )
    risk = RiskService(
        name="risk", event_bus=asyncio.Queue(), execution_service=execution
    )
    intent = OrderIntentPayload(
        intent_id="intent",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=0.001,
    )
    assert risk.evaluate_intent(intent).reason == "user_stream_disconnected"
    projection = build_runtime_projection(
        runtime_mode="runtime_primary",
        runtime_is_running=True,
        execution_source=execution,
    )
    assert projection.runtime_is_healthy is False
