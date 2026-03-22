from __future__ import annotations

from datetime import datetime, timezone

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


def test_gateway_connects_and_subscribes_user_data() -> None:
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


def test_gateway_normalizes_order_update_and_fill_events() -> None:
    manager = FakeWebSocketManager()
    gateway = BinanceUserDataGateway(websocket_manager=manager)
    order_events: list[RuntimeEvent] = []
    fill_events: list[RuntimeEvent] = []
    gateway.register_order_update_handler(order_events.append)
    gateway.register_fill_handler(fill_events.append)

    ts = datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc)

    async def run() -> None:
        await gateway.connect()
        await manager.callbacks["user"](
            {
                "type": "order_update",
                "symbol": "BTCUSDT",
                "order_id": "123",
                "client_order_id": "client-1",
                "side": "BUY",
                "status": "PARTIALLY_FILLED",
                "filled_quantity": 0.25,
                "remaining_quantity": 0.75,
                "avg_fill_price": 70000.0,
                "reduce_only": False,
                "timestamp": ts,
                "last_fill_qty": 0.10,
                "fill_price": 69950.0,
            }
        )

    import asyncio

    asyncio.run(run())

    assert len(order_events) == 1
    assert len(fill_events) == 1
    assert order_events[0].meta.symbol == "BTC/USDT"
    assert order_events[0].meta.event_type == "execution.order_update"
    assert isinstance(order_events[0].payload, OrderUpdatePayload)
    assert order_events[0].payload.status == "partially_filled"
    assert order_events[0].payload.filled_qty == 0.25
    assert order_events[0].payload.remaining_qty == 0.75
    assert isinstance(fill_events[0].payload, FillPayload)
    assert fill_events[0].payload.symbol == "BTC/USDT"
    assert fill_events[0].payload.side == "buy"
    assert fill_events[0].payload.fill_qty == 0.10
    assert fill_events[0].payload.fill_price == 69950.0


def test_gateway_derives_remaining_qty_from_raw_execution_report() -> None:
    manager = FakeWebSocketManager()
    gateway = BinanceUserDataGateway(websocket_manager=manager)
    order_events: list[RuntimeEvent] = []
    fill_events: list[RuntimeEvent] = []
    gateway.register_order_update_handler(order_events.append)
    gateway.register_fill_handler(fill_events.append)

    async def run() -> None:
        await gateway.connect()
        await manager.callbacks["user"](
            {
                "e": "executionReport",
                "s": "ETHUSDT",
                "i": "456",
                "c": "client-2",
                "S": "SELL",
                "X": "PARTIALLY_FILLED",
                "q": "2.0",
                "z": "0.75",
                "L": "3200.0",
                "l": "0.25",
            }
        )

    import asyncio

    asyncio.run(run())

    assert len(order_events) == 1
    assert len(fill_events) == 1
    assert order_events[0].payload.remaining_qty == 1.25
    assert fill_events[0].payload.symbol == "ETH/USDT"
    assert fill_events[0].payload.side == "sell"
    assert fill_events[0].payload.fill_qty == 0.25


def test_gateway_normalizes_balance_updates_and_ignores_noise() -> None:
    manager = FakeWebSocketManager()
    gateway = BinanceUserDataGateway(websocket_manager=manager)
    balance_events: list[RuntimeEvent] = []
    gateway.register_balance_update_handler(balance_events.append)

    async def run() -> None:
        await gateway.connect()
        await manager.callbacks["user"](
            {
                "type": "balance_update",
                "balances": [
                    {"asset": "USDT", "free": "125.0", "locked": "5.0"},
                    {"asset": "BTC", "free": "0.01", "locked": "0.0"},
                ],
                "timestamp": 1_743_480_000_000,
            }
        )
        await manager.callbacks["user"]({"type": "something_else"})

    import asyncio

    asyncio.run(run())

    assert len(balance_events) == 1
    assert balance_events[0].meta.event_type == "execution.balance_update"
    assert balance_events[0].meta.symbol is None
    assert isinstance(balance_events[0].payload, BalanceUpdatePayload)
    assert balance_events[0].payload.balances["USDT"]["free"] == 125.0
    assert balance_events[0].payload.balances["BTC"]["locked"] == 0.0
