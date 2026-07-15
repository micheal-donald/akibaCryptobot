"""Tests for the /api/ohlcv charting endpoint and UI asset versioning."""

import pandas as pd
import pytest
from fastapi import HTTPException

from api.app import _compute_asset_version
from api.routes.market import get_ohlcv


class FakeExchange:
    def __init__(self, df):
        self._df = df
        self.calls = []

    async def get_ohlcv(self, symbol, timeframe, limit):
        self.calls.append((symbol, timeframe, limit))
        return self._df


class FakeBot:
    def __init__(self, df, pairs=None):
        self.data_exchange = FakeExchange(df)
        self.config = {"trading": {"pairs": pairs or ["BTC/USDT", "ETH/USDT"]}}


def make_df():
    idx = pd.to_datetime([1752570000000, 1752570900000], unit="ms")
    return pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.5],
            "close": [101.0, 102.5],
            "volume": [10.0, 12.0],
        },
        index=idx,
    )


@pytest.mark.asyncio
async def test_returns_chart_ready_bars():
    bot = FakeBot(make_df())

    payload = await get_ohlcv(symbol="BTC/USDT", timeframe="15m", limit=300, bot=bot)

    assert payload["symbol"] == "BTC/USDT"
    assert payload["timeframe"] == "15m"
    assert len(payload["data"]) == 2
    first = payload["data"][0]
    assert first["time"] == 1752570000
    assert first["open"] == 100.0
    assert first["close"] == 101.0
    assert bot.data_exchange.calls == [("BTC/USDT", "15m", 300)]


@pytest.mark.asyncio
async def test_rejects_unconfigured_symbol():
    bot = FakeBot(make_df())

    with pytest.raises(HTTPException) as exc:
        await get_ohlcv(symbol="DOGE/USDT", timeframe="15m", limit=300, bot=bot)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_rejects_bad_timeframe():
    bot = FakeBot(make_df())

    with pytest.raises(HTTPException) as exc:
        await get_ohlcv(symbol="BTC/USDT", timeframe="3m", limit=300, bot=bot)

    assert exc.value.status_code == 422


def test_asset_version_prefers_deploy_sha(tmp_path):
    ui_dir = tmp_path / "src" / "ui"
    ui_dir.mkdir(parents=True)
    (ui_dir / "index.html").write_text("<html></html>")
    deploy_dir = tmp_path / ".deploy"
    deploy_dir.mkdir()
    (deploy_dir / "current_sha").write_text(
        "cb2f56e6c573eaad5e2bcdf5421630219abee9c6\n"
    )

    assert _compute_asset_version(str(ui_dir)) == "cb2f56e"


def test_asset_version_falls_back_to_mtime(tmp_path):
    ui_dir = tmp_path / "src" / "ui"
    ui_dir.mkdir(parents=True)
    (ui_dir / "index.html").write_text("<html></html>")

    version = _compute_asset_version(str(ui_dir))

    assert version.isdigit()
    assert int(version) > 0
