"""Bounded restart recovery and reconciliation for execution state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable
from ports.exchange import ExecutionGateway

from .base import BaseService
from .execution_service import ExecutionService
from .position_service import PositionService


RecoveryIssueKind = Literal[
    "restored",
    "orphaned_order",
    "unprotected_position",
    "reconcile_error",
    "skipped",
]


@dataclass(slots=True, frozen=True)
class RecoveryIssue:
    """Single bounded recovery or reconciliation outcome."""

    symbol: str
    kind: RecoveryIssueKind
    reason: str


@dataclass(slots=True, frozen=True)
class RecoveryReport:
    """Summary of a restart recovery pass."""

    processed_trades: int
    restored_symbols: tuple[str, ...]
    protected_symbols: tuple[str, ...]
    orphaned_symbols: tuple[str, ...]
    unprotected_symbols: tuple[str, ...]
    reconcile_error_symbols: tuple[str, ...]
    skipped_symbols: tuple[str, ...]
    issues: tuple[RecoveryIssue, ...]


@runtime_checkable
class _OpenTradeStoreLike(Protocol):
    async def load_open_trades(self) -> dict[str, dict[str, Any]]: ...


@dataclass(slots=True)
class RestartRecoveryService(BaseService):
    """Rebuild typed execution and position state after a restart."""

    trade_store: _OpenTradeStoreLike
    execution_gateway: ExecutionGateway
    execution_service: ExecutionService
    position_service: PositionService
    max_recovery_items: int = 100
    fetch_timeout_seconds: float = 5.0
    _last_report: RecoveryReport | None = field(default=None, init=False, repr=False)

    async def on_start(self) -> None:
        return None

    async def on_stop(self) -> None:
        return None

    async def recover(self) -> RecoveryReport:
        """Rehydrate open positions and reconcile them against the exchange."""

        open_trades = await self.trade_store.load_open_trades()
        restored_symbols: list[str] = []
        protected_symbols: list[str] = []
        orphaned_symbols: list[str] = []
        unprotected_symbols: list[str] = []
        reconcile_error_symbols: list[str] = []
        skipped_symbols: list[str] = []
        issues: list[RecoveryIssue] = []

        for index, (symbol, trade) in enumerate(open_trades.items()):
            if index >= self.max_recovery_items:
                skipped_symbols.append(symbol)
                issues.append(
                    RecoveryIssue(
                        symbol=symbol,
                        kind="skipped",
                        reason="recovery item limit reached",
                    )
                )
                continue

            position_key = str(trade.get("order_id") or symbol)
            self.execution_service.restore_order_from_trade(trade)
            position_state = self.position_service.restore_position_from_trade(
                trade,
                position_id=position_key,
            )
            restored_symbols.append(symbol)

            order_snapshot = await self._fetch_order_snapshot(trade)
            if order_snapshot is not None:
                normalized_status = str(order_snapshot.status).strip().lower()
                if normalized_status in {
                    "cancelled",
                    "canceled",
                    "rejected",
                    "expired",
                }:
                    self.execution_service.mark_order_orphaned(
                        position_key,
                        f"exchange order {normalized_status}",
                    )
                    self.position_service.mark_reconcile_error(position_key)
                    orphaned_symbols.append(symbol)
                    reconcile_error_symbols.append(symbol)
                    issues.append(
                        RecoveryIssue(
                            symbol=symbol,
                            kind="orphaned_order",
                            reason=f"exchange order {normalized_status}",
                        )
                    )
                    continue

                self.execution_service.restore_order_from_trade(
                    trade,
                    order_snapshot=order_snapshot,
                )

            protection_state = await self.execution_service.ensure_native_protection(
                position_key,
                force=True,
            )
            if (
                protection_state is not None
                and protection_state.status.value == "native_submitted"
                and protection_state.submitted_order_ids
            ):
                self.position_service.mark_protective_orders(
                    position_state.position_id,
                    protection_state.submitted_order_ids,
                    stop_loss=position_state.stop_loss,
                    take_profit=position_state.take_profit,
                    trailing_stop=position_state.trailing_stop,
                )
                protected_symbols.append(symbol)
            else:
                unprotected_symbols.append(symbol)
                issues.append(
                    RecoveryIssue(
                        symbol=symbol,
                        kind="unprotected_position",
                        reason="native protection missing after restart",
                    )
                )

        report = RecoveryReport(
            processed_trades=min(len(open_trades), self.max_recovery_items),
            restored_symbols=tuple(restored_symbols),
            protected_symbols=tuple(protected_symbols),
            orphaned_symbols=tuple(orphaned_symbols),
            unprotected_symbols=tuple(unprotected_symbols),
            reconcile_error_symbols=tuple(reconcile_error_symbols),
            skipped_symbols=tuple(skipped_symbols),
            issues=tuple(issues),
        )
        self._last_report = report
        return report

    async def _fetch_order_snapshot(self, trade: dict[str, Any]) -> Any:
        order_id = trade.get("order_id")
        if not order_id:
            return None

        symbol = str(trade["symbol"])
        try:
            return await asyncio.wait_for(
                self.execution_gateway.fetch_order(symbol, str(order_id)),
                timeout=self.fetch_timeout_seconds,
            )
        except Exception as exc:
            self.logger.warning(
                "Recovery snapshot failed for %s/%s: %s",
                symbol,
                order_id,
                exc,
            )
            return None

    def get_last_report(self) -> RecoveryReport | None:
        """Return the most recent recovery report."""

        return self._last_report
