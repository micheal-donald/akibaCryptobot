import asyncio

import pytest

from api.app import shutdown_trading_bot


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
