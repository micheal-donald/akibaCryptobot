from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from domain.events import (
    BookTickerPayload,
    EventMeta,
    HousekeepingPayload,
    KlinePayload,
    RuntimeEvent,
)
from domain.models import Candle, SymbolState


def test_event_meta_is_frozen_and_typed() -> None:
    ts = datetime.now(timezone.utc)
    meta = EventMeta(
        event_id="evt-1",
        event_type="market.kline",
        source="binance.ws.kline",
        symbol="BTCUSDT",
        ts_exchange=ts,
        ts_received=ts,
    )

    assert meta.event_id == "evt-1"
    assert meta.event_type == "market.kline"
    assert meta.symbol == "BTCUSDT"

    with pytest.raises(FrozenInstanceError):
        meta.event_id = "evt-2"  # type: ignore[misc]


def test_runtime_event_wraps_payload() -> None:
    ts = datetime.now(timezone.utc)
    payload = BookTickerPayload(
        bid_price=100.0,
        bid_qty=1.5,
        ask_price=100.2,
        ask_qty=2.0,
    )
    event = RuntimeEvent(
        meta=EventMeta(
            event_id="evt-2",
            event_type="market.tick",
            source="binance.ws.book_ticker",
            symbol="ETHUSDT",
            ts_exchange=ts,
            ts_received=ts,
        ),
        payload=payload,
    )

    assert event.meta.source == "binance.ws.book_ticker"
    assert event.payload == payload


def test_kline_and_housekeeping_payloads_are_dataclasses() -> None:
    ts = datetime.now(timezone.utc)
    kline = KlinePayload(
        interval="1m",
        open_time=ts,
        close_time=ts,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=10.0,
        is_closed=True,
    )
    housekeeping = HousekeepingPayload(reason="portfolio_snapshot")

    assert kline.interval == "1m"
    assert housekeeping.reason == "portfolio_snapshot"


def test_symbol_state_updates_book_ticker_and_spread() -> None:
    state = SymbolState(symbol="BTCUSDT")
    state.update_book_ticker(
        bid_price=100.0,
        bid_qty=1.0,
        ask_price=100.2,
        ask_qty=2.0,
        last_price=100.1,
    )

    assert state.best_bid == 100.0
    assert state.best_ask == 100.2
    assert state.last_price == 100.1
    assert state.spread_bps == pytest.approx(19.98001998, rel=1e-9)


def test_symbol_state_adds_candles_and_tracks_last_close() -> None:
    state = SymbolState(symbol="BTCUSDT")
    candle = Candle(
        open_time=datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 3, 21, 12, 1, tzinfo=timezone.utc),
        open=100.0,
        high=101.0,
        low=99.5,
        close=100.5,
        volume=25.0,
    )

    state.add_candle(candle)

    assert len(state.candles_1m) == 1
    assert state.candles_1m[-1] == candle
    assert state.last_kline_close == candle.close_time
    assert state.last_price == 100.5
