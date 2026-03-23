from __future__ import annotations

import asyncio

from domain.events import (
    OrderIntentPayload,
    OrderSubmittedPayload,
    RiskBlockedPayload,
    RuntimeEvent,
)
from domain.state import PositionState, PositionStatus
from services.risk_service import RiskService


def build_service(
    *,
    max_open_positions: int = 0,
    kill_switch_enabled: bool = False,
) -> tuple[RiskService, asyncio.Queue[RuntimeEvent]]:
    event_bus: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    service = RiskService(
        name="risk",
        event_bus=event_bus,
        max_open_positions=max_open_positions,
        kill_switch_enabled=kill_switch_enabled,
    )
    return service, event_bus


def build_intent(
    *,
    intent_id: str = "intent-1",
    symbol: str = "BTC/USDT",
    side: str = "buy",
    quantity: float = 0.01,
    order_type: str = "market",
    limit_price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    reduce_only: bool = False,
) -> OrderIntentPayload:
    return OrderIntentPayload(
        intent_id=intent_id,
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        order_type=order_type,  # type: ignore[arg-type]
        quantity=quantity,
        limit_price=limit_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        reduce_only=reduce_only,
    )


def build_open_position(
    *,
    symbol: str = "BTC/USDT",
    side: str = "buy",
    open_qty: float = 0.01,
    status: PositionStatus = PositionStatus.OPEN,
) -> PositionState:
    return PositionState(
        position_id="pos-1",
        symbol=symbol,
        side=side,
        requested_qty=open_qty,
        status=status,
        open_qty=open_qty,
    )


class FakeExecutionService:
    def __init__(self) -> None:
        self.submissions: list[OrderIntentPayload] = []

    async def submit_order_intent(
        self, intent: OrderIntentPayload
    ) -> OrderSubmittedPayload:
        self.submissions.append(intent)
        return OrderSubmittedPayload(
            client_order_id=intent.intent_id,
            exchange_order_id="order-1",
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            requested_qty=intent.quantity,
            requested_price=intent.limit_price,
            reduce_only=intent.reduce_only,
        )


def test_risk_service_allows_valid_intent_without_emitting_block_event() -> None:
    service, event_bus = build_service()
    intent = build_intent(stop_loss=64000.0, take_profit=70000.0)

    decision = service.evaluate_intent(intent)

    assert decision.allowed is True
    assert decision.reason is None
    assert event_bus.empty()
    assert service.get_decision_counts() == {"allowed": 1}


def test_risk_service_blocks_duplicate_open_position() -> None:
    service, event_bus = build_service()
    intent = build_intent()

    decision = service.evaluate_intent(
        intent, runtime_positions=(build_open_position(),)
    )

    assert decision.allowed is False
    assert decision.reason == "duplicate_symbol_position"
    event = event_bus.get_nowait()
    assert event.meta.event_type == "risk.blocked"
    assert isinstance(event.payload, RiskBlockedPayload)
    assert event.payload.intent_id == "intent-1"
    assert event.payload.reason == "duplicate_symbol_position"
    assert service.get_decision_counts()["blocked"] == 1


def test_risk_service_blocks_invalid_stop_loss_take_profit_pair() -> None:
    service, event_bus = build_service()
    intent = build_intent(stop_loss=70000.0, take_profit=64000.0)

    decision = service.evaluate_intent(intent)

    assert decision.allowed is False
    assert decision.reason == "invalid_stop_take_profit_combination"
    assert event_bus.get_nowait().meta.event_type == "risk.blocked"


def test_risk_service_blocks_invalid_quantity() -> None:
    service, event_bus = build_service()
    intent = build_intent(quantity=0.0)

    decision = service.evaluate_intent(intent)

    assert decision.allowed is False
    assert decision.reason == "invalid_quantity"
    assert event_bus.get_nowait().payload.reason == "invalid_quantity"


def test_risk_service_blocks_when_kill_switch_is_enabled() -> None:
    service, event_bus = build_service(kill_switch_enabled=True)
    intent = build_intent()

    decision = service.evaluate_intent(intent)

    assert decision.allowed is False
    assert decision.reason == "kill_switch_enabled"
    assert event_bus.get_nowait().payload.reason == "kill_switch_enabled"


def test_risk_service_blocks_when_max_open_positions_is_reached() -> None:
    service, event_bus = build_service(max_open_positions=1)
    intent = build_intent(symbol="ETH/USDT")

    decision = service.evaluate_intent(
        intent,
        runtime_positions=(build_open_position(symbol="BTC/USDT"),),
    )

    assert decision.allowed is False
    assert decision.reason == "max_open_positions"
    assert event_bus.get_nowait().payload.reason == "max_open_positions"


def test_risk_service_allows_reduce_only_close_for_open_symbol() -> None:
    service, event_bus = build_service()
    intent = build_intent(reduce_only=True, quantity=0.005, side="sell")

    decision = service.evaluate_intent(
        intent, runtime_positions=(build_open_position(),)
    )

    assert decision.allowed is True
    assert event_bus.empty()


def test_risk_service_blocks_reduce_only_same_side_as_open_position() -> None:
    service, event_bus = build_service()
    intent = build_intent(reduce_only=True, quantity=0.005, side="buy")

    decision = service.evaluate_intent(
        intent, runtime_positions=(build_open_position(side="buy"),)
    )

    assert decision.allowed is False
    assert decision.reason == "reduce_only_same_side"
    event = event_bus.get_nowait()
    assert isinstance(event.payload, RiskBlockedPayload)
    assert event.payload.reason == "reduce_only_same_side"


def test_risk_service_routes_only_approved_intents_to_execution() -> None:
    service, event_bus = build_service()
    execution_service = FakeExecutionService()
    service.execution_service = execution_service

    allowed_intent = build_intent(
        intent_id="intent-allowed",
        stop_loss=64000.0,
        take_profit=70000.0,
    )
    blocked_intent = build_intent(
        intent_id="intent-blocked",
        quantity=0.0,
    )

    async def run() -> (
        tuple[OrderSubmittedPayload | None, OrderSubmittedPayload | None]
    ):
        allowed = await service.submit_order_intent(allowed_intent)
        blocked = await service.submit_order_intent(blocked_intent)
        return allowed, blocked

    allowed, blocked = asyncio.run(run())

    assert allowed is not None
    assert blocked is None
    assert execution_service.submissions == [allowed_intent]
    assert event_bus.get_nowait().payload.reason == "invalid_quantity"
