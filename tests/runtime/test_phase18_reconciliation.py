from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from monitoring.reconciliation import ReconciliationManager
from runtime.view_models import (
    ProjectedPositionView,
    ProjectionSource,
    RuntimeProjection,
)


NOW = datetime(2026, 4, 9, 8, 15, tzinfo=timezone.utc)


class FakeExchange:
    def __init__(self, balance: dict[str, object]) -> None:
        self.balance = balance

    async def get_balance(self) -> dict[str, object]:
        return self.balance


class FakeTelegram:
    enabled = False


class FakeRuntimeReconcileBot:
    def __init__(
        self,
        *,
        projection: RuntimeProjection,
        balance: dict[str, object],
        runtime_recovery: dict[str, object] | None = None,
    ) -> None:
        self.exchange = FakeExchange(balance)
        self.telegram = FakeTelegram()
        self._background_tasks: set = set()
        self.active_trades: dict[str, dict] = {}
        self._projection = projection
        self._runtime_recovery = runtime_recovery

    def get_runtime_projection(self) -> RuntimeProjection:
        return self._projection

    def get_runtime_recovery_info(self):
        return self._runtime_recovery


def make_projection(
    *,
    positions: tuple[ProjectedPositionView, ...],
) -> RuntimeProjection:
    return RuntimeProjection(
        source=ProjectionSource.RUNTIME_PROJECTION,
        runtime_mode="runtime_primary",
        runtime_enabled=True,
        runtime_is_running=True,
        runtime_is_healthy=True,
        runtime_owns_entries=True,
        runtime_owns_exits=True,
        active_position_count=len(positions),
        protected_position_count=0,
        pending_exit_count=0,
        open_order_count=0,
        rejected_order_count=0,
        positions=positions,
        orders=(),
        runtime_start_status="running",
        runtime_start_pending=False,
    )


def make_position(
    *,
    symbol: str = "BTC/USDT",
    open_qty: float = 0.5,
    status: str = "open",
) -> ProjectedPositionView:
    return ProjectedPositionView(
        position_id="pos-1",
        symbol=symbol,
        side="buy",
        status=status,
        open_qty=open_qty,
        requested_qty=open_qty,
        avg_entry_price=65000.0,
        avg_exit_price=None,
        stop_loss=63000.0,
        take_profit=66000.0,
        trailing_stop=None,
        entry_order_ids=("order-1",),
        protective_order_ids=(),
        fees_paid=0.0,
        opened_at=NOW,
        closed_at=None,
        updated_at=NOW,
    )


def test_phase18_reconciliation_uses_runtime_projection_as_authority() -> None:
    bot = FakeRuntimeReconcileBot(
        projection=make_projection(positions=(make_position(),)),
        balance={
            "BTC": {"free": 0.5, "used": 0.0, "total": 0.5},
            "USDT": {"free": 1000.0, "used": 0.0, "total": 1000.0},
        },
        runtime_recovery={
            "processed_trades": 1,
            "restored_symbols": ["BTC/USDT"],
            "protected_symbols": [],
            "orphaned_symbols": [],
            "unprotected_symbols": [],
            "reconcile_error_symbols": [],
            "skipped_symbols": [],
            "issues": [],
            "issue_count": 0,
        },
    )

    result = asyncio.run(ReconciliationManager(bot).reconcile())

    assert result["source"] == "runtime_projection"
    assert result["lost_trades"] == []
    assert result["mismatches"] == []
    assert result["orphans"] == []
    assert result["runtime_drift"] == []
    assert result["runtime_recovery"]["processed_trades"] == 1


def test_phase18_reconciliation_surfaces_runtime_drift_and_exchange_orphans() -> None:
    bot = FakeRuntimeReconcileBot(
        projection=make_projection(
            positions=(make_position(status="reconcile_error"),)
        ),
        balance={
            "BTC": {"free": 0.0, "used": 0.0, "total": 0.0},
            "ETH": {"free": 0.25, "used": 0.0, "total": 0.25},
            "USDT": {"free": 1000.0, "used": 0.0, "total": 1000.0},
        },
        runtime_recovery={
            "processed_trades": 1,
            "restored_symbols": ["BTC/USDT"],
            "protected_symbols": [],
            "orphaned_symbols": ["BTC/USDT"],
            "unprotected_symbols": [],
            "reconcile_error_symbols": ["BTC/USDT"],
            "skipped_symbols": [],
            "issues": [
                {
                    "symbol": "BTC/USDT",
                    "kind": "orphaned_order",
                    "reason": "exchange order cancelled",
                }
            ],
            "issue_count": 1,
        },
    )

    result = asyncio.run(ReconciliationManager(bot).reconcile())

    assert result["source"] == "runtime_projection"
    assert result["lost_trades"][0]["symbol"] == "BTC/USDT"
    assert result["runtime_drift"][0]["kind"] == "reconcile_error"
    assert result["runtime_drift"][1]["kind"] == "orphaned_order"
    assert result["orphans"] == [
        {
            "asset": "ETH",
            "amount": 0.25,
            "reason": (
                "Asset found on Exchange with no active trade record in the "
                "authoritative state source"
            ),
        }
    ]
