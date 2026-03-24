import asyncio

import pytest

from api.app import shutdown_trading_bot
from api.routes.status import get_status


class FakeBot:
    def __init__(self):
        self.stop_calls = 0
        self.stop_event = asyncio.Event()

    def stop(self):
        self.stop_calls += 1
        self.stop_event.set()


@pytest.mark.asyncio
async def test_shutdown_trading_bot_waits_for_cooperative_exit():
    bot = FakeBot()
    cleanup_calls = 0

    async def run_bot():
        nonlocal cleanup_calls
        try:
            await bot.stop_event.wait()
        finally:
            cleanup_calls += 1

    trading_task = asyncio.create_task(run_bot())

    await shutdown_trading_bot(bot, trading_task, timeout_seconds=0.1)

    assert bot.stop_calls == 1
    assert trading_task.done() is True
    assert cleanup_calls == 1


@pytest.mark.asyncio
async def test_shutdown_trading_bot_cancels_when_task_does_not_exit():
    bot = FakeBot()

    async def run_forever():
        while True:
            await asyncio.sleep(1)

    trading_task = asyncio.create_task(run_forever())

    await shutdown_trading_bot(bot, trading_task, timeout_seconds=0.01)

    assert bot.stop_calls == 1
    assert trading_task.cancelled() is True


class FakeStatusBot:
    def __init__(self):
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.config = {"exchange": {"sandbox": True}}
        self.strategy_manager = type(
            "StrategyManager", (), {"active_strategy": "momentum"}
        )()

    def get_status_info(self):
        return {
            "active_trades": 0,
            "daily_pnl": 0.0,
            "uptime": "0:00:10",
            "is_running": True,
            "runtime_mode": "runtime_primary",
            "runtime_is_running": True,
            "runtime_owns_entries": True,
            "runtime_owns_exits": True,
        }


@pytest.mark.asyncio
async def test_status_route_includes_runtime_cutover_fields():
    bot = FakeStatusBot()

    payload = await get_status(bot=bot)

    assert payload["runtime_mode"] == "runtime_primary"
    assert payload["runtime_is_running"] is True
    assert payload["runtime_owns_entries"] is True
    assert payload["runtime_owns_exits"] is True
    assert payload["strategy"] == "momentum"
    assert payload["trading_mode"] == "paper"
