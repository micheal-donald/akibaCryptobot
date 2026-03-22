from __future__ import annotations

from datetime import datetime, timezone

import pytest

from adapters.binance.user_data_gateway import BinanceUserDataGateway
from domain.events import (
    BalanceUpdatePayload,
    FillPayload,
    OrderUpdatePayload,
    RuntimeEvent,
)


class FakeWebSocketManager:
    def __init__(self) -> None:
        self.callbacks: dict[str, object] = {}
        self.calls: list[object] = []

    async def connect(self) -> None:
        self.calls.append("connect")

    async def disconnect(self) -> None:
        self.calls.append("disconnect")

    def register_callback(self, event_type: str, callback: object) -> None:
        self.callbacks[event_type] = callback

    async def subscribe_user_data(self) -> None:
        self.calls.append("subscribe_user_data")


def test_gateway_connects_registers_bridge_and_proxies_subscription() -> None:
    manager = FakeWebSocketManager()
    gateway = BinanceUserDataGateway(websocket_manager=manager)

    async def run() -> None:
        await gateway.connect()
        await gateway.subscribe_user_stream()
        await gateway.disconnect()

    import asyncio

    asyncio.run(run())

    assert manager.calls == ["connect", "subscribe_user_data", "disconnect"]
    assert "user" in manager.callbacks


def test_gateway_normalizes_balance_updates() -> None:
    manager = FakeWebSocketManager()
    gateway = BinanceUserDataGateway(websocket_manager=manager)
    balance_events: list[RuntimeEvent] = []
    gateway.register_balance_update_handler(balance_events.append)

    event_time_ms = 1_743_480_000_000
    expected_event_time = datetime.fromtimestamp(event_time_ms / 1000, tz=timezone.utc)

    async def run() -> None:
        await gateway.connect()
        await manager.callbacks["user"](
            {
                "e": "outboundAccountPosition",
                "E": event_time_ms,
                "B": [
                    {"a": "USDT", "f": "100.5", "l": "2.5"},
                    {"a": "BTC", "f": "0.01", "l": "0"},
                ],
            }
        )

    import asyncio

    asyncio.run(run())

    assert len(balance_events) == 1
    assert balance_events[0].meta.event_type == "execution.balance_update"
    assert balance_events[0].meta.ts_exchange == expected_event_time
    assert isinstance(balance_events[0].payload, BalanceUpdatePayload)
    assert balance_events[0].payload.balances["USDT"]["free"] == 100.5
    assert balance_events[0].payload.balances["BTC"]["locked"] == 0.0


def test_gateway_normalizes_order_updates_and_fills_from_raw_execution_report() -> None:
    manager = FakeWebSocketManager()
    gateway = BinanceUserDataGateway(websocket_manager=manager)
    order_events: list[RuntimeEvent] = []
    fill_events: list[RuntimeEvent] = []
    gateway.register_order_update_handler(order_events.append)
    gateway.register_fill_handler(fill_events.append)

    event_time_ms = 1_743_480_120_500
    trade_time_ms = 1_743_480_120_000
    expected_trade_time = datetime.fromtimestamp(trade_time_ms / 1000, tz=timezone.utc)

    async def run() -> None:
        await gateway.connect()
        await manager.callbacks["user"](
            {
                "e": "executionReport",
                "E": event_time_ms,
                "T": trade_time_ms,
                "s": "BTCUSDT",
                "c": "client-1",
                "i": 123456,
                "S": "BUY",
                "X": "PARTIALLY_FILLED",
                "x": "TRADE",
                "q": "0.50",
                "z": "0.20",
                "l": "0.20",
                "L": "65010.5",
                "Z": "13002.1",
                "n": "0.12",
                "N": "USDT",
                "m": True,
                "R": False,
            }
        )

    import asyncio

    asyncio.run(run())

    assert len(order_events) == 1
    assert order_events[0].meta.event_type == "execution.order_update"
    assert order_events[0].meta.symbol == "BTC/USDT"
    assert order_events[0].meta.ts_exchange == expected_trade_time
    assert isinstance(order_events[0].payload, OrderUpdatePayload)
    assert order_events[0].payload.status == "partially_filled"
    assert order_events[0].payload.filled_qty == 0.2
    assert order_events[0].payload.remaining_qty == 0.3
    assert order_events[0].payload.avg_fill_price == pytest.approx(65010.5)
    assert order_events[0].payload.raw_status == "PARTIALLY_FILLED"

    assert len(fill_events) == 1
    assert fill_events[0].meta.event_type == "execution.fill"
    assert fill_events[0].meta.symbol == "BTC/USDT"
    assert isinstance(fill_events[0].payload, FillPayload)
    assert fill_events[0].payload.fill_qty == 0.2
    assert fill_events[0].payload.fill_price == 65010.5
    assert fill_events[0].payload.cumulative_qty == 0.2
    assert fill_events[0].payload.cumulative_quote_qty == 13002.1
    assert fill_events[0].payload.fee_cost == 0.12
    assert fill_events[0].payload.fee_currency == "USDT"
    assert fill_events[0].payload.is_maker is True


def test_gateway_normalizes_parsed_execution_report_payloads() -> None:
    manager = FakeWebSocketManager()
    gateway = BinanceUserDataGateway(websocket_manager=manager)
    order_events: list[RuntimeEvent] = []
    fill_events: list[RuntimeEvent] = []
    gateway.register_order_update_handler(order_events.append)
    gateway.register_fill_handler(fill_events.append)

    ts = datetime(2026, 3, 22, 9, 10, tzinfo=timezone.utc)

    async def run() -> None:
        await gateway.connect()
        await manager.callbacks["user"](
            {
                "type": "order_update",
                "symbol": "ETHUSDT",
                "client_order_id": "client-2",
                "order_id": "777",
                "side": "SELL",
                "status": "FILLED",
                "execution_type": "TRADE",
                "quantity": 1.0,
                "filled_quantity": 1.0,
                "last_filled_quantity": 0.4,
                "last_fill_price": 2200.5,
                "cumulative_quote_quantity": 2200.5,
                "fee_cost": 0.5,
                "fee_currency": "USDT",
                "is_maker": False,
                "reduce_only": True,
                "trade_time": ts,
            }
        )

    import asyncio

    asyncio.run(run())

    assert len(order_events) == 1
    assert order_events[0].meta.symbol == "ETH/USDT"
    assert order_events[0].payload.status == "filled"
    assert order_events[0].payload.remaining_qty == 0.0
    assert order_events[0].payload.reduce_only is True

    assert len(fill_events) == 1
    assert fill_events[0].meta.symbol == "ETH/USDT"
    assert fill_events[0].payload.side == "sell"
    assert fill_events[0].payload.fill_qty == 0.4
    assert fill_events[0].payload.fill_price == 2200.5
    assert fill_events[0].payload.reduce_only is True


def test_gateway_ignores_irrelevant_user_events() -> None:
    manager = FakeWebSocketManager()
    gateway = BinanceUserDataGateway(websocket_manager=manager)
    order_events: list[RuntimeEvent] = []
    fill_events: list[RuntimeEvent] = []
    balance_events: list[RuntimeEvent] = []
    gateway.register_order_update_handler(order_events.append)
    gateway.register_fill_handler(fill_events.append)
    gateway.register_balance_update_handler(balance_events.append)

    async def run() -> None:
        await gateway.connect()
        await manager.callbacks["user"]({"e": "listenKeyExpired"})

    import asyncio

    asyncio.run(run())

    assert order_events == []
    assert fill_events == []
    assert balance_events == []
