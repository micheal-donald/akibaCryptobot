"""Microstructure feature helpers for replayed order-book research."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from runtime.depth_capture import CapturedDepthEvent
from runtime.depth_replay import DepthReplayStep
from services.order_book_service import OrderBookLevel, OrderBookSnapshot


@dataclass(slots=True, frozen=True)
class MicrostructureFeatureVector:
    """Compact microstructure feature vector for a replayed snapshot."""

    event_id: str
    symbol: str
    sequence: int | None
    spread: float | None
    spread_bps: float | None
    depth_imbalance: float | None
    trade_pressure: float | None
    trade_count: int
    buy_volume: float
    sell_volume: float


@dataclass(slots=True, frozen=True)
class _TradeSample:
    symbol: str | None
    side: str
    quantity: float


def spread_from_snapshot(snapshot: OrderBookSnapshot) -> float | None:
    """Return the absolute best-ask minus best-bid spread."""

    best_bid = snapshot.best_bid
    best_ask = snapshot.best_ask
    if best_bid is None or best_ask is None:
        return None
    return best_ask.price - best_bid.price


def spread_bps_from_snapshot(snapshot: OrderBookSnapshot) -> float | None:
    """Return the spread in basis points relative to the mid price."""

    spread = spread_from_snapshot(snapshot)
    if spread is None:
        return None
    mid_price = snapshot.mid_price
    if mid_price is None or mid_price == 0:
        return None
    return spread / mid_price * 10_000


def depth_imbalance(
    snapshot: OrderBookSnapshot, *, depth: int | None = None
) -> float | None:
    """Return the bid-vs-ask imbalance for the configured depth window."""

    bid_levels = _slice_levels(snapshot.bids, depth)
    ask_levels = _slice_levels(snapshot.asks, depth)
    bid_volume = sum(level.quantity for level in bid_levels if level.quantity > 0)
    ask_volume = sum(level.quantity for level in ask_levels if level.quantity > 0)
    total_volume = bid_volume + ask_volume
    if total_volume <= 0:
        return None
    return (bid_volume - ask_volume) / total_volume


def trade_pressure(
    source: Iterable[
        DepthReplayStep | CapturedDepthEvent | Mapping[str, object] | _TradeSample
    ],
    *,
    symbol: str | None = None,
    window: int | None = None,
) -> float | None:
    """Return the recent buy/sell pressure for the supplied trade sequence."""

    samples = tuple(_iter_trade_samples(source, symbol=symbol))
    if window is not None and window > 0:
        samples = samples[-window:]
    return _pressure_from_samples(samples)


def feature_vector_from_replay(
    steps: Iterable[DepthReplayStep],
    *,
    depth_window: int | None = 5,
    trade_window: int = 10,
) -> tuple[MicrostructureFeatureVector, ...]:
    """Build feature vectors from replayed depth and trade steps."""

    if trade_window <= 0:
        raise ValueError("trade_window must be positive")
    if depth_window is not None and depth_window <= 0:
        raise ValueError("depth_window must be positive when provided")

    recent_trades: dict[str, deque[_TradeSample]] = {}
    feature_rows: list[MicrostructureFeatureVector] = []

    for step in steps:
        record = step.record
        trade_sample = _trade_sample_from_step(step)
        if trade_sample is not None and trade_sample.symbol is not None:
            bucket = recent_trades.setdefault(
                trade_sample.symbol,
                deque(maxlen=trade_window),
            )
            bucket.append(trade_sample)

        if step.snapshot is None:
            continue

        snapshot = step.snapshot
        depth_trades = tuple(recent_trades.get(snapshot.symbol, ()))
        pressure = _pressure_from_samples(depth_trades)
        buy_volume = sum(
            sample.quantity for sample in depth_trades if sample.side == "buy"
        )
        sell_volume = sum(
            sample.quantity for sample in depth_trades if sample.side == "sell"
        )
        feature_rows.append(
            MicrostructureFeatureVector(
                event_id=record.event_id,
                symbol=snapshot.symbol,
                sequence=snapshot.sequence,
                spread=spread_from_snapshot(snapshot),
                spread_bps=spread_bps_from_snapshot(snapshot),
                depth_imbalance=depth_imbalance(snapshot, depth=depth_window),
                trade_pressure=pressure,
                trade_count=len(depth_trades),
                buy_volume=buy_volume,
                sell_volume=sell_volume,
            )
        )

    return tuple(feature_rows)


def _trade_sample_from_step(
    step: DepthReplayStep,
) -> _TradeSample | None:
    return _trade_sample_from_record(step.record)


def _trade_sample_from_record(record: CapturedDepthEvent) -> _TradeSample | None:
    if record.payload_type != "TradeEvent":
        return None
    payload = record.payload
    if not isinstance(payload, Mapping):
        return None
    side = payload.get("side")
    quantity = payload.get("quantity")
    if not isinstance(side, str):
        return None
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
        return None
    return _TradeSample(
        symbol=record.symbol,
        side=side.lower(),
        quantity=float(quantity),
    )


def _iter_trade_samples(
    source: Iterable[DepthReplayStep | CapturedDepthEvent | Mapping[str, object]],
    *,
    symbol: str | None = None,
) -> Iterable[_TradeSample]:
    for item in source:
        sample = _trade_sample_from_source(item)
        if sample is None:
            continue
        if symbol is not None and sample.symbol != symbol:
            continue
        yield sample


def _trade_sample_from_source(
    item: DepthReplayStep | CapturedDepthEvent | Mapping[str, object] | _TradeSample,
) -> _TradeSample | None:
    if isinstance(item, _TradeSample):
        return item
    if isinstance(item, DepthReplayStep):
        return _trade_sample_from_record(item.record)
    if isinstance(item, CapturedDepthEvent):
        return _trade_sample_from_record(item)
    if not isinstance(item, Mapping):
        return None
    payload = item.get("payload")
    if not isinstance(payload, Mapping):
        return None
    side = payload.get("side")
    quantity = payload.get("quantity")
    if not isinstance(side, str):
        return None
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
        return None
    symbol = item.get("symbol")
    return _TradeSample(
        symbol=symbol if isinstance(symbol, str) else None,
        side=side.lower(),
        quantity=float(quantity),
    )


def _slice_levels(
    levels: Sequence[OrderBookLevel], depth: int | None
) -> Sequence[OrderBookLevel]:
    if depth is None:
        return levels
    return levels[:depth]


def _pressure_from_samples(samples: Sequence[_TradeSample]) -> float | None:
    if not samples:
        return None
    buy_volume = sum(sample.quantity for sample in samples if sample.side == "buy")
    sell_volume = sum(sample.quantity for sample in samples if sample.side == "sell")
    total_volume = buy_volume + sell_volume
    if total_volume <= 0:
        return None
    return (buy_volume - sell_volume) / total_volume
