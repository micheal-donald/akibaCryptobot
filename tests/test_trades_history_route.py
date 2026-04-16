"""Pagination tests for the /api/trades/history route."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio

from api.routes.performance import get_trade_history
from persistence.trade_store import TradeStore


@pytest_asyncio.fixture
async def seeded_bot(tmp_path):
    """Bot stub wrapping a TradeStore seeded with 5 closed trades."""
    db_path = str(tmp_path / "history_route.db")
    store = TradeStore(db_path)
    await store.initialize()

    for i in range(5):
        symbol = f"SYM{i}/USDT"
        await store.insert_trade(
            {
                "symbol": symbol,
                "side": "buy",
                "amount": 1.0,
                "entry_price": 100.0,
                "timestamp": datetime.now().isoformat(),
            }
        )
        await store.update_trade_exit(
            symbol,
            exit_price=110.0,
            exit_reason="take_profit",
            pnl=10.0,
            fees=0.0,
        )

    # Mirror the minimal shape api.routes.performance.get_trade_history relies on.
    bot = SimpleNamespace(trade_store=store, performance_tracker=None)
    yield bot
    await store.close()


@pytest.mark.asyncio
async def test_trade_history_returns_newest_first_and_envelope(seeded_bot):
    payload = await get_trade_history(limit=2, offset=0, bot=seeded_bot)

    assert payload["total"] == 5
    assert payload["offset"] == 0
    assert payload["limit"] == 2
    assert payload["count"] == 2
    assert payload["has_more"] is True

    symbols = [t["symbol"] for t in payload["trades"]]
    assert symbols == ["SYM4/USDT", "SYM3/USDT"]


@pytest.mark.asyncio
async def test_trade_history_honours_offset(seeded_bot):
    payload = await get_trade_history(limit=2, offset=2, bot=seeded_bot)

    assert payload["offset"] == 2
    assert [t["symbol"] for t in payload["trades"]] == ["SYM2/USDT", "SYM1/USDT"]
    assert payload["has_more"] is True


@pytest.mark.asyncio
async def test_trade_history_last_page_clears_has_more(seeded_bot):
    payload = await get_trade_history(limit=2, offset=4, bot=seeded_bot)

    assert payload["count"] == 1
    assert payload["trades"][0]["symbol"] == "SYM0/USDT"
    assert payload["has_more"] is False


@pytest.mark.asyncio
async def test_trade_history_past_end_returns_empty_window(seeded_bot):
    payload = await get_trade_history(limit=2, offset=99, bot=seeded_bot)

    assert payload["total"] == 5
    assert payload["count"] == 0
    assert payload["trades"] == []
    assert payload["has_more"] is False


@pytest.mark.asyncio
async def test_trade_history_empty_bot_returns_zeroed_envelope():
    """If bot exposes neither trade_store nor performance_tracker, the
    envelope still has the pagination fields the frontend expects."""
    bot = SimpleNamespace(trade_store=None, performance_tracker=None)

    payload = await get_trade_history(limit=20, offset=0, bot=bot)

    assert payload == {
        "count": 0,
        "total": 0,
        "offset": 0,
        "limit": 20,
        "has_more": False,
        "trades": [],
    }
