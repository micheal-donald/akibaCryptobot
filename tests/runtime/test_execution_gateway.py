from __future__ import annotations

import asyncio

from adapters.binance.execution_gateway import BinanceExecutionGateway
from domain.events import OrderIntentPayload
from ports.exchange import ProtectionIntent


class FakeExchange:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, float, float | None, dict | None]] = []

    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict | None = None,
    ) -> dict[str, object]:
        self.calls.append((symbol, type, side, amount, price, params))
        return {
            "id": f"{type}-1",
            "clientOrderId": f"client-{type}",
            "symbol": symbol,
            "amount": amount,
            "price": price,
            "status": "open",
            "reduceOnly": bool((params or {}).get("reduceOnly")),
        }


class FakeExecutionClient:
    def __init__(self) -> None:
        self.exchange = FakeExchange()
        self.markets_loaded = 0
        self.market_orders: list[tuple[str, str, float, float | None]] = []
        self.limit_orders: list[tuple[str, str, float, float]] = []
        self.cancel_orders: list[tuple[str, str]] = []
        self.fetch_orders: list[tuple[str, str]] = []
        self.close_calls = 0
        self.market_order_response: dict[str, object] = {
            "id": "market-1",
            "clientOrderId": "intent-1",
            "symbol": "BTC/USDT",
            "amount": 0.25,
            "price": 100.5,
            "status": "closed",
            "reduceOnly": False,
        }
        self.limit_order_response: dict[str, object] = {
            "id": "limit-1",
            "clientOrderId": "intent-2",
            "symbol": "ETH/USDT",
            "amount": 1.0,
            "price": 200.0,
            "status": "open",
            "reduceOnly": True,
        }
        self.cancel_order_response: dict[str, object] = {
            "id": "limit-1",
            "clientOrderId": "intent-2",
            "symbol": "ETH/USDT",
            "status": "canceled",
            "filledQty": 0.0,
            "remaining": 0.0,
            "avgPrice": None,
            "reduceOnly": False,
            "raw_status": "CANCELED",
        }
        self.fetch_order_response: dict[str, object] = {
            "id": "limit-1",
            "clientOrderId": "intent-2",
            "symbol": "ETH/USDT",
            "status": "partially_filled",
            "filledQty": 0.4,
            "remaining": 0.6,
            "avgPrice": 201.5,
            "reduceOnly": False,
        }

    async def ensure_markets_loaded(self) -> None:
        self.markets_loaded += 1

    async def place_market_order(
        self, symbol: str, side: str, amount: float, price: float | None = None
    ) -> dict[str, object]:
        self.market_orders.append((symbol, side, amount, price))
        return self.market_order_response

    async def place_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> dict[str, object]:
        self.limit_orders.append((symbol, side, amount, price))
        return self.limit_order_response

    async def cancel_order(self, order_id: str, symbol: str) -> dict[str, object]:
        self.cancel_orders.append((order_id, symbol))
        return self.cancel_order_response

    async def get_order_status(self, order_id: str, symbol: str) -> dict[str, object]:
        self.fetch_orders.append((order_id, symbol))
        return self.fetch_order_response

    async def close(self) -> None:
        self.close_calls += 1


def test_execution_gateway_submits_market_orders_with_canonical_symbols() -> None:
    client = FakeExecutionClient()
    gateway = BinanceExecutionGateway(client=client)
    intent = OrderIntentPayload(
        intent_id="intent-1",
        symbol="BTCUSDT",
        side="buy",
        order_type="market",
        quantity=0.25,
        strategy="ensemble",
    )

    async def run():
        await gateway.connect()
        return await gateway.submit_order(intent)

    submitted = asyncio.run(run())

    assert client.markets_loaded == 1
    assert client.market_orders == [("BTC/USDT", "buy", 0.25, None)]
    assert submitted.client_order_id == "intent-1"
    assert submitted.exchange_order_id == "market-1"
    assert submitted.symbol == "BTC/USDT"
    assert submitted.side == "buy"
    assert submitted.order_type == "market"
    assert submitted.requested_qty == 0.25
    assert submitted.requested_price == 100.5


def test_execution_gateway_submits_limit_orders_and_normalizes_updates() -> None:
    client = FakeExecutionClient()
    gateway = BinanceExecutionGateway(client=client)
    intent = OrderIntentPayload(
        intent_id="intent-2",
        symbol="ETHUSDT",
        side="sell",
        order_type="limit",
        quantity=1.0,
        limit_price=200.0,
        reduce_only=True,
    )

    async def run():
        submitted = await gateway.submit_order(intent)
        cancelled = await gateway.cancel_order("ETHUSDT", "limit-1")
        fetched = await gateway.fetch_order("ETHUSDT", "limit-1")
        return submitted, cancelled, fetched

    submitted, cancelled, fetched = asyncio.run(run())

    assert client.limit_orders == [("ETH/USDT", "sell", 1.0, 200.0)]
    assert submitted.order_type == "limit"
    assert submitted.reduce_only is True
    assert cancelled.status == "canceled"
    assert cancelled.symbol == "ETH/USDT"
    assert cancelled.exchange_order_id == "limit-1"
    assert cancelled.filled_qty == 0.0
    assert cancelled.remaining_qty == 0.0
    assert cancelled.raw_status == "CANCELED"
    assert fetched.status == "partially_filled"
    assert fetched.filled_qty == 0.4
    assert fetched.remaining_qty == 0.6
    assert fetched.avg_fill_price == 201.5


def test_execution_gateway_best_effort_protection_uses_native_orders() -> None:
    client = FakeExecutionClient()
    gateway = BinanceExecutionGateway(client=client)
    protection = ProtectionIntent(
        stop_loss_price=99.0,
        take_profit_price=110.0,
        trailing_stop_price=95.0,
    )

    async def run():
        return await gateway.submit_protection("BTC/USDT", "buy", 0.25, protection)

    submitted = asyncio.run(run())

    assert len(submitted) == 3
    assert [order.order_type for order in submitted] == [
        "stop_loss_limit",
        "take_profit_limit",
        "trailing_stop_market",
    ]
    assert client.exchange.calls == [
        (
            "BTC/USDT",
            "stop_loss_limit",
            "sell",
            0.25,
            99.0,
            {
                "stopPrice": 99.0,
                "activationPrice": 99.0,
                "reduceOnly": True,
            },
        ),
        (
            "BTC/USDT",
            "take_profit_limit",
            "sell",
            0.25,
            110.0,
            {
                "stopPrice": 110.0,
                "activationPrice": 110.0,
                "reduceOnly": True,
            },
        ),
        (
            "BTC/USDT",
            "trailing_stop_market",
            "sell",
            0.25,
            None,
            {
                "stopPrice": 95.0,
                "activationPrice": 95.0,
                "reduceOnly": True,
            },
        ),
    ]


def test_execution_gateway_can_close_when_requested() -> None:
    client = FakeExecutionClient()
    gateway = BinanceExecutionGateway(client=client, close_on_disconnect=True)

    async def run() -> None:
        await gateway.disconnect()

    asyncio.run(run())

    assert client.close_calls == 1
