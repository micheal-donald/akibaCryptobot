from __future__ import annotations

from typing import Literal

from domain.events import (
    OrderIntentPayload,
    OrderSubmittedPayload,
    OrderUpdatePayload,
)
from ports.exchange import (
    ExecutionGateway,
    MarketDataGateway,
    ProtectionIntent,
    UserStreamGateway,
)


class FakeMarketDataGateway:
    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    def register_callback(self, event_type: str, callback: object) -> None:
        del event_type, callback

    async def subscribe_book_ticker(self, symbol: str) -> None:
        del symbol

    async def subscribe_kline(self, symbol: str, interval: str = "1m") -> None:
        del symbol, interval


class FakeExecutionGateway:
    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def submit_order(self, intent: OrderIntentPayload) -> OrderSubmittedPayload:
        return OrderSubmittedPayload(
            client_order_id=intent.intent_id,
            exchange_order_id="123",
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            requested_qty=intent.quantity,
            requested_price=intent.limit_price,
            reduce_only=intent.reduce_only,
        )

    async def cancel_order(
        self, symbol: str, exchange_order_id: str
    ) -> OrderUpdatePayload:
        return OrderUpdatePayload(
            client_order_id="intent-1",
            exchange_order_id=exchange_order_id,
            symbol=symbol,
            status="cancelled",
            filled_qty=0.0,
            remaining_qty=0.0,
        )

    async def fetch_order(
        self, symbol: str, exchange_order_id: str
    ) -> OrderUpdatePayload:
        return OrderUpdatePayload(
            client_order_id="intent-1",
            exchange_order_id=exchange_order_id,
            symbol=symbol,
            status="accepted",
            filled_qty=0.0,
            remaining_qty=1.0,
        )

    async def submit_protection(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        quantity: float,
        protection: ProtectionIntent,
    ) -> tuple[OrderSubmittedPayload, ...]:
        del protection
        return (
            OrderSubmittedPayload(
                client_order_id=f"protect-{symbol}",
                exchange_order_id="321",
                symbol=symbol,
                side=side,
                order_type="stop_loss_limit",
                requested_qty=quantity,
            ),
        )


class FakeUserStreamGateway:
    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def subscribe_user_stream(self) -> None:
        return None

    def register_order_update_handler(self, handler: object) -> None:
        del handler

    def register_fill_handler(self, handler: object) -> None:
        del handler

    def register_balance_update_handler(self, handler: object) -> None:
        del handler


def test_execution_gateway_protocol_is_structurally_implementable() -> None:
    gateway = FakeExecutionGateway()

    assert isinstance(gateway, ExecutionGateway)


def test_user_stream_gateway_protocol_is_structurally_implementable() -> None:
    gateway = FakeUserStreamGateway()

    assert isinstance(gateway, UserStreamGateway)


def test_market_data_gateway_protocol_remains_structurally_implementable() -> None:
    gateway = FakeMarketDataGateway()

    assert isinstance(gateway, MarketDataGateway)


def test_protection_intent_captures_native_exit_preferences() -> None:
    protection = ProtectionIntent(
        stop_loss_price=100.0,
        take_profit_price=120.0,
        trailing_stop_price=98.0,
    )

    assert protection.stop_loss_price == 100.0
    assert protection.take_profit_price == 120.0
    assert protection.trailing_stop_price == 98.0
    assert protection.native_preferred is True
