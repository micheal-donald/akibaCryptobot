from __future__ import annotations

from datetime import datetime, timezone

from services.order_book_service import (
    OrderBookLevel,
    OrderBookService,
)


def build_service(*, default_depth_window: int = 3) -> OrderBookService:
    return OrderBookService(
        name="order-book",
        default_depth_window=default_depth_window,
    )


def test_order_book_service_tracks_snapshot_and_best_levels() -> None:
    service = build_service()
    ts = datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)

    snapshot = service.replace_snapshot(
        "BTCUSDT",
        bids=[
            (100.0, 1.5),
            {"price": 99.5, "quantity": 2.0},
            OrderBookLevel(price=99.0, quantity=3.0),
        ],
        asks=[
            (100.5, 1.0),
            {"price": 101.0, "quantity": 2.5},
            OrderBookLevel(price=101.5, quantity=3.5),
        ],
        sequence=42,
        exchange_timestamp=ts,
        received_at=ts,
    )

    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.best_bid == OrderBookLevel(price=100.0, quantity=1.5)
    assert snapshot.best_ask == OrderBookLevel(price=100.5, quantity=1.0)
    assert snapshot.mid_price == 100.25
    assert snapshot.spread == 0.5
    assert snapshot.sequence == 42
    assert snapshot.exchange_timestamp == ts
    assert snapshot.received_at == ts

    best_bid, best_ask = service.get_best_bid_ask("BTCUSDT")
    assert best_bid == OrderBookLevel(price=100.0, quantity=1.5)
    assert best_ask == OrderBookLevel(price=100.5, quantity=1.0)


def test_order_book_service_honors_depth_windows() -> None:
    service = build_service(default_depth_window=2)

    service.replace_snapshot(
        "ETHUSDT",
        bids=[
            (100.0, 1.0),
            (99.5, 2.0),
            (99.0, 3.0),
        ],
        asks=[
            (100.5, 1.2),
            (101.0, 2.2),
            (101.5, 3.2),
        ],
    )

    snapshot = service.get_snapshot("ETHUSDT")
    assert snapshot is not None
    assert snapshot.bids == (
        OrderBookLevel(price=100.0, quantity=1.0),
        OrderBookLevel(price=99.5, quantity=2.0),
    )
    assert snapshot.asks == (
        OrderBookLevel(price=100.5, quantity=1.2),
        OrderBookLevel(price=101.0, quantity=2.2),
    )

    depth_window = service.get_depth_window("ETHUSDT", depth=1)
    assert depth_window == (
        (OrderBookLevel(price=100.0, quantity=1.0),),
        (OrderBookLevel(price=100.5, quantity=1.2),),
    )


def test_order_book_service_updates_and_removes_levels() -> None:
    service = build_service()

    service.replace_snapshot(
        "BNBUSDT",
        bids=[(600.0, 1.0)],
        asks=[(600.5, 1.5)],
    )
    service.apply_level_update("BNBUSDT", "bid", 600.0, 2.25)
    service.apply_level_update("BNBUSDT", "ask", 600.5, 0.0)
    service.apply_level_update("BNBUSDT", "ask", 601.0, 3.5)

    snapshot = service.get_snapshot("BNBUSDT")
    assert snapshot is not None
    assert snapshot.best_bid == OrderBookLevel(price=600.0, quantity=2.25)
    assert snapshot.best_ask == OrderBookLevel(price=601.0, quantity=3.5)
    assert snapshot.bids == (OrderBookLevel(price=600.0, quantity=2.25),)
    assert snapshot.asks == (OrderBookLevel(price=601.0, quantity=3.5),)


def test_order_book_service_returns_none_for_missing_symbols() -> None:
    service = build_service()

    assert service.get_snapshot("DOGEUSDT") is None
    assert service.get_best_bid_ask("DOGEUSDT") == (None, None)
    assert service.get_depth_window("DOGEUSDT") is None
