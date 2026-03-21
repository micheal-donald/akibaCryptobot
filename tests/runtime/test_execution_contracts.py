from __future__ import annotations

from datetime import datetime, timezone

from domain.events import (
    FillPayload,
    OrderIntentPayload,
    OrderSubmittedPayload,
    OrderUpdatePayload,
    PositionClosedPayload,
    PositionOpenedPayload,
    RiskBlockedPayload,
)
from domain.state import OrderState, OrderStatus, PositionState, PositionStatus


def test_order_intent_payload_captures_protection_fields() -> None:
    payload = OrderIntentPayload(
        intent_id="intent-1",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=0.01,
        stop_loss=64000.0,
        take_profit=70000.0,
        strategy="ensemble",
        reason="breakout",
    )

    assert payload.intent_id == "intent-1"
    assert payload.stop_loss == 64000.0
    assert payload.take_profit == 70000.0
    assert payload.reduce_only is False


def test_order_and_fill_payloads_capture_normalized_execution_fields() -> None:
    submitted = OrderSubmittedPayload(
        client_order_id="client-1",
        exchange_order_id="123",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        requested_qty=0.01,
    )
    update = OrderUpdatePayload(
        client_order_id="client-1",
        exchange_order_id="123",
        symbol="BTC/USDT",
        status="accepted",
        filled_qty=0.0,
        remaining_qty=0.01,
    )
    fill = FillPayload(
        client_order_id="client-1",
        exchange_order_id="123",
        symbol="BTC/USDT",
        side="buy",
        fill_qty=0.01,
        fill_price=65000.0,
        cumulative_qty=0.01,
        cumulative_quote_qty=650.0,
        fee_cost=0.65,
        fee_currency="USDT",
    )

    assert submitted.exchange_order_id == "123"
    assert update.status == "accepted"
    assert fill.fill_price == 65000.0
    assert fill.fee_currency == "USDT"


def test_position_and_risk_payloads_capture_lifecycle_outcomes() -> None:
    opened = PositionOpenedPayload(
        position_id="pos-1",
        symbol="BTC/USDT",
        side="buy",
        quantity=0.01,
        avg_entry_price=65000.0,
        entry_order_ids=("123",),
    )
    closed = PositionClosedPayload(
        position_id="pos-1",
        symbol="BTC/USDT",
        side="buy",
        quantity=0.01,
        avg_entry_price=65000.0,
        avg_exit_price=66000.0,
        close_reason="take_profit",
        pnl=10.0,
        fee_cost=1.3,
    )
    blocked = RiskBlockedPayload(
        intent_id="intent-1",
        symbol="BTC/USDT",
        reason="max_open_trades",
    )

    assert opened.entry_order_ids == ("123",)
    assert closed.close_reason == "take_profit"
    assert blocked.reason == "max_open_trades"


def test_order_state_defaults_to_created_status() -> None:
    now = datetime.now(timezone.utc)
    state = OrderState(
        client_order_id="client-1",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        requested_qty=0.01,
        requested_price=65000.0,
        created_at=now,
        updated_at=now,
    )

    assert state.status == OrderStatus.CREATED
    assert state.filled_qty == 0.0
    assert state.order_id is None


def test_position_state_defaults_to_new_status() -> None:
    now = datetime.now(timezone.utc)
    state = PositionState(
        position_id="pos-1",
        symbol="BTC/USDT",
        side="buy",
        requested_qty=0.01,
        stop_loss=64000.0,
        take_profit=70000.0,
        updated_at=now,
    )

    assert state.status == PositionStatus.NEW
    assert state.open_qty == 0.0
    assert state.protective_order_ids == ()
