#!/usr/bin/env python3
"""
Microstructure Decision Profitability Analysis

Reads persisted microstructure.decision events from the runtime store and
computes hypothetical mark-out PnL after fees for each decision, bucketed by
decision profile (confidence, depth imbalance, spread). Use the report to
tighten runtime.microstructure.strategy thresholds in config/config.json so
only profiles that clear fees keep generating decisions.

Mark-out prices come from the decision stream itself (decision_price is the
book mid at decision time, persisted many times per minute per symbol) merged
with closed klines from runtime_candles. For a buy decision at price P, the
gross mark-out at horizon H is the first price observation at or after
decision_time + H (within a staleness cap), expressed in bps relative to P
(sign-flipped for sells). Net PnL subtracts a configurable round-trip cost;
note decision_price is a mid price, so size --fee-bps to include fees AND the
half-spread you would pay per side when crossing.

Usage:
    python tools/analyze_microstructure_decisions.py
    python tools/analyze_microstructure_decisions.py --db data/runtime.db --since-hours 24
    python tools/analyze_microstructure_decisions.py --fee-bps 15 --horizons 1 5 15
"""

import argparse
import json
import sqlite3
import sys
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean, median


INTERVAL_UNIT_MINUTES = {"s": 1 / 60, "m": 1, "h": 60, "d": 1440}


@dataclass(frozen=True)
class Decision:
    symbol: str
    ts: datetime
    action: str
    confidence: float
    spread_bps: float | None
    depth_imbalance: float | None
    trade_pressure: float | None
    decision_price: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze persisted microstructure.decision profitability"
    )
    parser.add_argument("--db", default="data/runtime.db", help="Runtime store path")
    parser.add_argument(
        "--since-hours",
        type=float,
        default=None,
        help="Only analyze decisions from the last N hours (default: all)",
    )
    parser.add_argument(
        "--fee-bps",
        type=float,
        default=20.0,
        help="Round-trip fee in bps (Binance spot taker 0.1%%/side = 20)",
    )
    parser.add_argument(
        "--horizons",
        type=float,
        nargs="+",
        default=[1.0, 5.0, 15.0],
        help="Mark-out horizons in minutes",
    )
    parser.add_argument(
        "--primary-horizon",
        type=float,
        default=5.0,
        help="Horizon used for the threshold grid search",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=20,
        help="Minimum decisions for a threshold combo to be ranked",
    )
    parser.add_argument(
        "--suggest-json",
        action="store_true",
        help="Print the best threshold combo as a config JSON snippet",
    )
    return parser.parse_args()


def parse_ts(raw: str) -> datetime:
    ts = datetime.fromisoformat(raw)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def interval_minutes(interval: str) -> float:
    unit = interval[-1]
    if unit not in INTERVAL_UNIT_MINUTES:
        return float("inf")
    try:
        return float(interval[:-1]) * INTERVAL_UNIT_MINUTES[unit]
    except ValueError:
        return float("inf")


def load_decisions(conn: sqlite3.Connection, since: datetime | None) -> list[Decision]:
    query = (
        "SELECT symbol, ts_received, payload_json FROM runtime_events "
        "WHERE event_type = 'microstructure.decision'"
    )
    params: tuple = ()
    if since is not None:
        query += " AND ts_received >= ?"
        params = (since.isoformat(),)
    query += " ORDER BY ts_received ASC"

    decisions = []
    for symbol, ts_received, payload_json in conn.execute(query, params):
        payload = json.loads(payload_json)
        price = payload.get("decision_price")
        if price is None or price <= 0:
            continue
        decisions.append(
            Decision(
                symbol=symbol or payload.get("symbol", "?"),
                ts=parse_ts(ts_received),
                action=payload.get("action", "?"),
                confidence=float(payload.get("confidence", 0.0)),
                spread_bps=payload.get("spread_bps"),
                depth_imbalance=payload.get("depth_imbalance"),
                trade_pressure=payload.get("trade_pressure"),
                decision_price=float(price),
            )
        )
    return decisions


def load_price_series(
    conn: sqlite3.Connection, decisions: list[Decision]
) -> dict[str, tuple[list[datetime], list[float]]]:
    """Per symbol, merge decision mid prices with candle closes, time-sorted."""
    points: dict[str, list[tuple[datetime, float]]] = {}
    for decision in decisions:
        points.setdefault(decision.symbol, []).append(
            (decision.ts, decision.decision_price)
        )

    intervals: dict[str, str] = {}
    for symbol, interval in conn.execute(
        "SELECT DISTINCT symbol, interval FROM runtime_candles"
    ):
        current = intervals.get(symbol)
        if current is None or interval_minutes(interval) < interval_minutes(current):
            intervals[symbol] = interval
    for symbol, interval in intervals.items():
        for close_time, close in conn.execute(
            "SELECT close_time, close FROM runtime_candles "
            "WHERE symbol = ? AND interval = ?",
            (symbol, interval),
        ):
            points.setdefault(symbol, []).append((parse_ts(close_time), float(close)))

    series: dict[str, tuple[list[datetime], list[float]]] = {}
    for symbol, pairs in points.items():
        pairs.sort(key=lambda pair: pair[0])
        series[symbol] = ([t for t, _ in pairs], [p for _, p in pairs])
    return series


def markout_bps(
    decision: Decision,
    prices: dict[str, tuple[list[datetime], list[float]]],
    horizon_minutes: float,
) -> float | None:
    series = prices.get(decision.symbol)
    if series is None:
        return None
    times, values = series
    target = decision.ts + timedelta(minutes=horizon_minutes)
    idx = bisect_left(times, target)
    if idx >= len(times):
        return None
    # Reject stale mark-outs: the observation must land within one extra
    # horizon of the target or the measured horizon is no longer comparable.
    if times[idx] - target > timedelta(minutes=horizon_minutes):
        return None
    gross = (values[idx] / decision.decision_price - 1.0) * 1e4
    return gross if decision.action == "buy" else -gross


def summarize(values: list[float], fee_bps: float) -> str:
    if not values:
        return "n=0"
    net = [v - fee_bps for v in values]
    wins = sum(1 for v in net if v > 0)
    return (
        f"n={len(values):4d}  gross mean={mean(values):+7.2f}  "
        f"median={median(values):+7.2f}  net mean={mean(net):+7.2f}  "
        f"net win%={100.0 * wins / len(net):5.1f}"
    )


def bucket_report(
    title: str,
    decisions: list[Decision],
    markouts: dict[int, float],
    key,
    edges: list[float],
    fee_bps: float,
) -> None:
    print(f"\n  By {title}:")
    labels = [f"[{lo:g}, {hi:g})" for lo, hi in zip(edges, edges[1:])] + [
        f"[{edges[-1]:g}, inf)"
    ]
    buckets: dict[str, list[float]] = {label: [] for label in labels}
    for i, decision in enumerate(decisions):
        if i not in markouts:
            continue
        value = key(decision)
        if value is None:
            continue
        idx = bisect_right(edges, value) - 1
        if idx < 0:
            continue
        idx = min(idx, len(labels) - 1)
        buckets[labels[idx]].append(markouts[i])
    for label, values in buckets.items():
        print(f"    {label:>14}: {summarize(values, fee_bps)}")


def grid_search(
    decisions: list[Decision],
    markouts: dict[int, float],
    fee_bps: float,
    min_count: int,
) -> list[tuple[float, float, float, int, float, float]]:
    """Rank (min_confidence, min_abs_imbalance, max_spread_bps) combos by net mean."""
    results = []
    for min_conf in (0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9):
        for min_imb in (0.2, 0.3, 0.4, 0.5, 0.6):
            for max_spread in (1.0, 2.0, 5.0, 10.0, 30.0):
                net = [
                    markouts[i] - fee_bps
                    for i, d in enumerate(decisions)
                    if i in markouts
                    and d.confidence >= min_conf
                    and d.depth_imbalance is not None
                    and abs(d.depth_imbalance) >= min_imb
                    and d.spread_bps is not None
                    and d.spread_bps <= max_spread
                ]
                if len(net) < min_count:
                    continue
                results.append(
                    (min_conf, min_imb, max_spread, len(net), mean(net), sum(net))
                )
    results.sort(key=lambda row: row[4], reverse=True)
    return results


def main() -> int:
    args = parse_args()
    since = None
    if args.since_hours is not None:
        since = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)

    conn = sqlite3.connect(args.db)
    try:
        decisions = load_decisions(conn, since)
        if not decisions:
            print("No microstructure.decision events found.")
            return 1
        prices = load_price_series(conn, decisions)
    finally:
        conn.close()

    span = decisions[-1].ts - decisions[0].ts
    print(
        f"Loaded {len(decisions)} decisions "
        f"({decisions[0].ts:%Y-%m-%d %H:%M} .. {decisions[-1].ts:%Y-%m-%d %H:%M} UTC, "
        f"{span.total_seconds() / 3600:.1f}h) | fee {args.fee_bps:g} bps round-trip"
    )

    for horizon in args.horizons:
        markouts: dict[int, float] = {}
        for i, decision in enumerate(decisions):
            value = markout_bps(decision, prices, horizon)
            if value is not None:
                markouts[i] = value
        coverage = 100.0 * len(markouts) / len(decisions)
        print(f"\n=== Horizon {horizon:g}m (mark-out coverage {coverage:.0f}%) ===")
        print(f"  Overall: {summarize(list(markouts.values()), args.fee_bps)}")

        for action in sorted({d.action for d in decisions}):
            values = [
                markouts[i]
                for i, d in enumerate(decisions)
                if i in markouts and d.action == action
            ]
            print(f"  {action:>7}: {summarize(values, args.fee_bps)}")
        for symbol in sorted({d.symbol for d in decisions}):
            values = [
                markouts[i]
                for i, d in enumerate(decisions)
                if i in markouts and d.symbol == symbol
            ]
            print(f"  {symbol:>9}: {summarize(values, args.fee_bps)}")

        bucket_report(
            "confidence",
            decisions,
            markouts,
            lambda d: d.confidence,
            [0.55, 0.7, 0.85, 0.95],
            args.fee_bps,
        )
        bucket_report(
            "|depth_imbalance|",
            decisions,
            markouts,
            lambda d: abs(d.depth_imbalance) if d.depth_imbalance is not None else None,
            [0.2, 0.4, 0.6, 0.8],
            args.fee_bps,
        )
        bucket_report(
            "spread_bps",
            decisions,
            markouts,
            lambda d: d.spread_bps,
            [0.0, 1.0, 5.0, 10.0],
            args.fee_bps,
        )

    primary_markouts: dict[int, float] = {}
    for i, decision in enumerate(decisions):
        value = markout_bps(decision, prices, args.primary_horizon)
        if value is not None:
            primary_markouts[i] = value

    ranked = grid_search(decisions, primary_markouts, args.fee_bps, args.min_count)
    print(
        f"\n=== Threshold grid search ({args.primary_horizon:g}m horizon, "
        f"min n={args.min_count}) ==="
    )
    if not ranked:
        print("  No combo met the minimum decision count yet — collect more data.")
        return 0
    print("  min_conf  min_|imb|  max_spread      n   net mean   net total")
    for min_conf, min_imb, max_spread, n, net_mean, net_total in ranked[:10]:
        print(
            f"  {min_conf:8.2f}  {min_imb:9.2f}  {max_spread:10.1f}  "
            f"{n:5d}  {net_mean:+9.2f}  {net_total:+10.1f}"
        )

    best = ranked[0]
    if best[4] <= 0:
        print(
            "\n  WARNING: no threshold combo has positive net mean after fees. "
            "Do not loosen execution assumptions — keep paper-shadow only."
        )
    if args.suggest_json:
        print("\nSuggested runtime.microstructure.strategy snippet:")
        print(
            json.dumps(
                {
                    "min_confidence": best[0],
                    "min_abs_imbalance": best[1],
                    "max_spread_bps": best[2],
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
