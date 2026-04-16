"""Performance metrics and low-latency reporting endpoints."""

import inspect

from fastapi import APIRouter, Depends, Query

from api.dependencies import get_bot

router = APIRouter()


def _build_low_latency_fallback(bot):
    projection_getter = getattr(bot, "get_runtime_projection", None)
    projection = projection_getter() if callable(projection_getter) else None
    telemetry_getter = getattr(bot, "get_runtime_telemetry_info", None)
    telemetry = telemetry_getter() if callable(telemetry_getter) else None
    event_latency = None
    if isinstance(telemetry, dict):
        latency = telemetry.get("latency", {})
        event_age = latency.get("event_age_ms", {})
        if isinstance(event_age, dict):
            event_latency = event_age.get("average_ms")
    return {
        "available": False,
        "source": "api.performance.low_latency",
        "runtime_mode": getattr(projection, "runtime_mode", "legacy"),
        "state_source": getattr(getattr(projection, "source", None), "value", "legacy"),
        "runtime_is_running": getattr(projection, "runtime_is_running", False),
        "runtime_is_healthy": getattr(projection, "runtime_is_healthy", True),
        "decision_latency_ms": event_latency,
        "submit_latency_ms": None,
        "fill_quality_bps": None,
        "spread_capture_bps": None,
        "capture_health": None,
        "report": None,
    }


@router.get("/metrics")
async def get_metrics(bot=Depends(get_bot)):
    """
    Get comprehensive performance metrics:
    Sharpe ratio, Sortino ratio, profit factor, win rate, max drawdown, etc.
    """
    if hasattr(bot, "performance_tracker") and bot.performance_tracker:
        return await bot.performance_tracker.get_metrics_summary(
            trade_store=bot.trade_store
        )

    # Fallback: return strategy-level performance from strategy manager
    return bot.strategy_manager.get_all_performance()


@router.get("/equity-curve")
async def get_equity_curve(bot=Depends(get_bot)):
    """Get time-series equity data for charting."""
    if hasattr(bot, "performance_tracker") and bot.performance_tracker:
        curve = bot.performance_tracker.equity_curve
        return {
            "data": [
                {
                    "timestamp": ts.isoformat(),
                    "balance": balance,
                }
                for ts, balance in curve
            ]
        }

    # Fallback: use portfolio value history
    return {
        "data": [
            {"index": i, "balance": val}
            for i, val in enumerate(bot.portfolio_value_history)
        ]
    }


def _pnl_percent(side: str, entry_price: float, exit_price: float) -> float:
    if not entry_price:
        return 0.0
    if side == "buy":
        return ((exit_price - entry_price) / entry_price) * 100
    return ((entry_price - exit_price) / entry_price) * 100


@router.get("/trades/history")
async def get_trade_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    bot=Depends(get_bot),
):
    """Get completed trade log, newest first, with pagination.

    Prefers the persistent trade store when available (deep history),
    falling back to the in-memory performance tracker cache (capped at
    the last 100 trades).
    """
    trade_store = getattr(bot, "trade_store", None)
    if trade_store is not None:
        total = await trade_store.count_closed_trades()
        rows = await trade_store.load_closed_trades(
            limit=limit, offset=offset, order="desc"
        )
        trades_payload = []
        for t in rows:
            entry_time = t.get("timestamp")
            exit_time = t.get("exit_timestamp") or entry_time
            pnl = float(t.get("pnl") or 0.0)
            pnl_pct = _pnl_percent(
                t.get("side", ""),
                float(t.get("entry_price") or 0.0),
                float(t.get("exit_price") or 0.0),
            )
            duration = str(exit_time - entry_time) if entry_time and exit_time else ""
            trades_payload.append(
                {
                    "symbol": t.get("symbol"),
                    "side": t.get("side"),
                    "entry_price": t.get("entry_price"),
                    "exit_price": t.get("exit_price"),
                    "amount": t.get("amount"),
                    "pnl": pnl,
                    "pnl_percent": pnl_pct,
                    "exit_reason": t.get("exit_reason"),
                    "strategy": "ensemble",
                    "entry_time": entry_time.isoformat() if entry_time else None,
                    "exit_time": exit_time.isoformat() if exit_time else None,
                    "duration": duration,
                }
            )
        return {
            "count": len(trades_payload),
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(trades_payload) < total,
            "trades": trades_payload,
        }

    if hasattr(bot, "performance_tracker") and bot.performance_tracker:
        # In-memory fallback: the tracker keeps the last N trades in
        # chronological order, so slice from the end to get newest first.
        all_trades = list(bot.performance_tracker.trades)
        total = len(all_trades)
        newest_first = list(reversed(all_trades))
        window = newest_first[offset : offset + limit]
        return {
            "count": len(window),
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(window) < total,
            "trades": [
                {
                    "symbol": t.symbol,
                    "side": t.side,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "amount": t.amount,
                    "pnl": t.pnl,
                    "pnl_percent": t.pnl_percent,
                    "exit_reason": t.exit_reason,
                    "strategy": t.strategy,
                    "entry_time": t.entry_time.isoformat(),
                    "exit_time": t.exit_time.isoformat(),
                    "duration": str(t.duration),
                }
                for t in window
            ],
        }
    return {
        "count": 0,
        "total": 0,
        "offset": offset,
        "limit": limit,
        "has_more": False,
        "trades": [],
    }


@router.get("/strategy/performance")
async def get_strategy_performance(bot=Depends(get_bot)):
    """Get per-strategy performance breakdown."""
    return bot.strategy_manager.get_all_performance()


@router.get("/low-latency")
async def get_low_latency_performance(bot=Depends(get_bot)):
    """Get low-latency replay/shadow performance if available."""
    getter = getattr(bot, "get_low_latency_performance", None)
    if callable(getter):
        report = getter()
        if inspect.isawaitable(report):
            report = await report
        if hasattr(report, "as_dict"):
            return report.as_dict()
        if isinstance(report, dict):
            return report
        return {"available": True, "report": report}

    return _build_low_latency_fallback(bot)
