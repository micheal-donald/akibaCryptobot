from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from fastapi import WebSocketDisconnect

from api.dependencies import bot_state
from api.routes.status import get_status
from api.routes.trading import get_active_trades
from api.websockets import ws_status
from domain.state import OrderState, OrderStatus, PositionState, PositionStatus
from notifications.telegram_bot import TelegramNotifier
from runtime.mode import RuntimeMode
from runtime.projection import build_runtime_projection


NOW = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class StateSource:
    positions: tuple[PositionState, ...] = ()
    orders: tuple[OrderState, ...] = ()

    def get_all_position_states(self) -> tuple[PositionState, ...]:
        return self.positions

    def get_all_order_states(self) -> tuple[OrderState, ...]:
        return self.orders


class FakeMessage:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    async def reply_text(self, text, parse_mode=None):
        self.calls.append({"text": text, "parse_mode": parse_mode})


class FakeUpdate:
    def __init__(self):
        self.message = FakeMessage()


class FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.sent: list[dict[str, object]] = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        self.sent.append(payload)
        raise WebSocketDisconnect(code=1000)


class FakePerformanceTracker:
    async def get_metrics_summary(self, trade_store=None):
        return {
            "initial_balance": 1000.0,
            "current_balance": 1010.0,
            "total_pnl": 10.0,
            "total_return_percent": 1.0,
            "total_trades": 3,
            "winning_trades": 2,
            "losing_trades": 1,
            "win_rate": 66.7,
            "average_win": 8.0,
            "average_loss": -6.0,
            "win_loss_ratio": 1.33,
            "sharpe_ratio": 1.2,
            "sortino_ratio": 1.5,
            "profit_factor": 1.8,
            "expectancy": 3.3,
            "max_drawdown_percent": 2.5,
            "current_drawdown_percent": 0.5,
            "open_trades_count": 7,
        }


def build_position(
    *,
    position_id: str,
    status: PositionStatus,
    open_qty: float,
) -> PositionState:
    return PositionState(
        position_id=position_id,
        symbol="BTC/USDT",
        side="buy",
        requested_qty=0.25,
        status=status,
        open_qty=open_qty,
        avg_entry_price=65000.0,
        stop_loss=64000.0,
        take_profit=67000.0,
        entry_order_ids=("entry-1",),
        protective_order_ids=(),
        opened_at=NOW,
        updated_at=NOW,
    )


def build_order(*, order_id: str, status: OrderStatus) -> OrderState:
    return OrderState(
        client_order_id=order_id,
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        requested_qty=0.25,
        status=status,
        order_id=f"ex-{order_id}",
        filled_qty=0.25,
        avg_fill_price=65000.0,
        reduce_only=False,
        parent_position_id="pos-1",
        requested_price=65000.0,
        created_at=NOW,
        updated_at=NOW,
    )


class ProjectionBot:
    def __init__(self):
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.config = {"exchange": {"sandbox": True}}
        self.strategy_manager = type(
            "StrategyManager", (), {"active_strategy": "ensemble"}
        )()
        self.trade_store = object()
        self.performance_tracker = FakePerformanceTracker()
        self._legacy_trades = [
            {"symbol": "ETH/USDT", "side": "buy", "amount": 1.5, "entry_price": 3200.0},
            {"symbol": "SOL/USDT", "side": "sell", "amount": 2.0, "entry_price": 150.0},
        ]
        self._projection = build_runtime_projection(
            runtime_mode=RuntimeMode.LEGACY,
            runtime_is_running=False,
        )

    def set_projection(self, projection):
        self._projection = projection

    def get_status_info(self):
        return {
            "active_trades": len(self._legacy_trades),
            "daily_pnl": 12.34,
            "uptime": "0:10:00",
            "is_running": True,
            "trading_mode": "paper",
        }

    def get_runtime_projection(self):
        return self._projection

    def get_active_trades(self):
        return list(self._legacy_trades)


def build_projection(mode: RuntimeMode):
    if mode is RuntimeMode.LEGACY:
        return build_runtime_projection(
            runtime_mode=mode,
            runtime_is_running=False,
        )

    if mode is RuntimeMode.SHADOW:
        position = build_position(
            position_id="shadow-pos",
            status=PositionStatus.OPEN,
            open_qty=0.25,
        )
        order = build_order(order_id="shadow-order", status=OrderStatus.ACCEPTED)
        return build_runtime_projection(
            runtime_mode=mode,
            runtime_is_running=True,
            position_source=StateSource(positions=(position,)),
            execution_source=StateSource(orders=(order,)),
        )

    if mode is RuntimeMode.RUNTIME_ENTRIES:
        position = build_position(
            position_id="entry-pos",
            status=PositionStatus.ENTRY_PENDING,
            open_qty=0.25,
        )
        order = build_order(order_id="entry-order", status=OrderStatus.ACCEPTED)
        return build_runtime_projection(
            runtime_mode=mode,
            runtime_is_running=True,
            position_source=StateSource(positions=(position,)),
            execution_source=StateSource(orders=(order,)),
        )

    position = build_position(
        position_id="primary-pos",
        status=PositionStatus.OPEN,
        open_qty=0.25,
    )
    order = build_order(order_id="primary-order", status=OrderStatus.ACCEPTED)
    return build_runtime_projection(
        runtime_mode=mode,
        runtime_is_running=True,
        position_source=StateSource(positions=(position,)),
        execution_source=StateSource(orders=(order,)),
    )


async def _capture_status_and_trades(bot: ProjectionBot):
    status_payload = await get_status(bot=bot)
    trades_payload = await get_active_trades(bot=bot)
    websocket = FakeWebSocket()

    bot_state["bot"] = bot
    await ws_status(websocket)

    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier.bot_instance = bot
    status_update = FakeUpdate()
    trades_update = FakeUpdate()
    performance_update = FakeUpdate()

    await notifier.cmd_status(status_update, None)
    await notifier.cmd_trades(trades_update, None)
    await notifier.cmd_performance(performance_update, None)

    return {
        "status": status_payload,
        "trades": trades_payload,
        "websocket": websocket.sent[0]["data"],
        "telegram_status": status_update.message.calls[0]["text"],
        "telegram_trades": trades_update.message.calls[0]["text"],
        "telegram_performance": performance_update.message.calls[0]["text"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "mode, expected_source, expected_active_trades, "
        "expected_projected_count, expected_performance_open_positions"
    ),
    [
        (RuntimeMode.LEGACY, "legacy", 2, 0, 7),
        (RuntimeMode.SHADOW, "shadow", 2, 1, 7),
        (RuntimeMode.RUNTIME_ENTRIES, "runtime_projection", 1, 1, 1),
        (RuntimeMode.RUNTIME_PRIMARY, "runtime_projection", 1, 1, 1),
    ],
)
async def test_phase7_projection_end_to_end_modes(
    mode,
    expected_source,
    expected_active_trades,
    expected_projected_count,
    expected_performance_open_positions,
):
    bot = ProjectionBot()
    bot.set_projection(build_projection(mode))

    payloads = await _capture_status_and_trades(bot)

    assert payloads["status"]["state_source"] == expected_source
    assert payloads["status"]["active_trades"] == expected_active_trades
    assert payloads["status"]["projected_active_positions"] == expected_projected_count
    assert payloads["status"]["projected_open_orders"] == expected_projected_count
    assert payloads["trades"]["source"] == (
        expected_source if mode is not RuntimeMode.SHADOW else "legacy"
    )
    assert payloads["trades"]["runtime_mode"] == mode.value
    assert payloads["websocket"]["state_source"] == expected_source
    assert payloads["websocket"]["active_trades"] == expected_active_trades
    assert f"Runtime Mode: {mode.value}" in payloads["telegram_status"]
    assert f"State Source: {expected_source}" in payloads["telegram_status"]
    assert (
        f"Open Positions: {expected_performance_open_positions}"
        in payloads["telegram_performance"]
    )


@pytest.mark.asyncio
async def test_phase7_rollback_restores_legacy_operator_surfaces():
    bot = ProjectionBot()
    bot.set_projection(build_projection(RuntimeMode.RUNTIME_PRIMARY))
    before = await _capture_status_and_trades(bot)

    bot.set_projection(build_projection(RuntimeMode.LEGACY))
    after = await _capture_status_and_trades(bot)

    assert before["status"]["state_source"] == "runtime_projection"
    assert before["status"]["active_trades"] == 1
    assert after["status"]["state_source"] == "legacy"
    assert after["status"]["active_trades"] == 2
    assert after["trades"]["source"] == "legacy"
    assert after["websocket"]["state_source"] == "legacy"
    assert "State Source: legacy" in after["telegram_status"]
    assert "Open Positions: 7" in after["telegram_performance"]
