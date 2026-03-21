from __future__ import annotations

from datetime import datetime, timezone

from domain.models import SymbolState
from services.shadow_comparator import ShadowComparator
from services.telemetry_service import TelemetryService


def _make_state(
    symbol: str,
    *,
    best_bid: float,
    best_ask: float,
    last_price: float,
    last_kline_close: datetime,
) -> SymbolState:
    state = SymbolState(symbol=symbol)
    state.best_bid = best_bid
    state.best_ask = best_ask
    state.last_price = last_price
    state.last_kline_close = last_kline_close
    state.last_update_at = last_kline_close
    state.last_book_ticker_at = last_kline_close
    return state


def test_shadow_comparator_reports_drift_and_missing_symbols() -> None:
    telemetry = TelemetryService(name="telemetry")
    current_close = datetime(2026, 3, 21, 12, 1, tzinfo=timezone.utc)
    legacy_close = datetime(2026, 3, 21, 12, 1, tzinfo=timezone.utc)
    current_state = _make_state(
        "BTC/USDT",
        best_bid=100.0,
        best_ask=100.2,
        last_price=100.1,
        last_kline_close=current_close,
    )
    legacy_state = _make_state(
        "BTC/USDT",
        best_bid=99.0,
        best_ask=99.2,
        last_price=99.1,
        last_kline_close=legacy_close,
    )

    comparator = ShadowComparator(
        name="shadow-comparator",
        current_state_provider=lambda: (current_state,),
        legacy_state_provider=lambda: {
            "BTC/USDT": legacy_state,
            "ETH/USDT": _make_state(
                "ETH/USDT",
                best_bid=50.0,
                best_ask=50.2,
                last_price=50.1,
                last_kline_close=legacy_close,
            ),
        },
        telemetry_service=telemetry,
        comparison_interval_seconds=10.0,
        drift_tolerance_bps=1.0,
    )

    async def run() -> tuple[str, ...]:
        return await comparator.compare_once()

    import asyncio

    drift_symbols = asyncio.run(run())

    assert drift_symbols == ("BTC/USDT",)
    assert comparator.get_comparison_runs() == 1
    assert comparator.get_drift_count() == 1
    assert comparator.get_missing_symbol_count() == 1
    assert comparator.get_last_drift_symbols() == ("BTC/USDT",)
    assert telemetry.get_metric_count("shadow_drift.BTC/USDT") == 1
    assert telemetry.get_metric_count("missing_symbol.ETH/USDT") == 1


def test_shadow_comparator_can_run_without_drift() -> None:
    state = _make_state(
        "BTC/USDT",
        best_bid=100.0,
        best_ask=100.2,
        last_price=100.1,
        last_kline_close=datetime(2026, 3, 21, 12, 1, tzinfo=timezone.utc),
    )
    comparator = ShadowComparator(
        name="shadow-comparator",
        current_state_provider=lambda: (state,),
        legacy_state_provider=lambda: {"BTC/USDT": state},
        comparison_interval_seconds=10.0,
    )

    async def run() -> tuple[str, ...]:
        return await comparator.compare_once()

    import asyncio

    drift_symbols = asyncio.run(run())

    assert drift_symbols == ()
    assert comparator.get_comparison_runs() == 1
    assert comparator.get_drift_count() == 0
    assert comparator.get_missing_symbol_count() == 0
