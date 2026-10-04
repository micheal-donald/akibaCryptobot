"""Runtime risk gate for order intents."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from typing import Iterable

from domain.events import (
    EventMeta,
    OrderIntentPayload,
    OrderSubmittedPayload,
    RiskBlockedPayload,
    RuntimeEvent,
)
from domain.state import PositionState, PositionStatus

from .base import BaseService
from .execution_service import ExecutionService


@dataclass(slots=True)
class RiskDecision:
    """Structured outcome for a runtime risk evaluation."""

    allowed: bool
    reason: str | None = None


@dataclass(slots=True)
class RiskService(BaseService):
    """Evaluate order intents before execution consumes them."""

    event_bus: asyncio.Queue[RuntimeEvent]
    execution_service: ExecutionService | None = None
    position_source: object | None = None
    max_open_positions: int = 0
    kill_switch_enabled: bool = False
    entries_paused: bool = False
    _decision_counts: Counter[str] = field(
        default_factory=Counter, init=False, repr=False
    )

    async def on_start(self) -> None:
        return None

    async def on_stop(self) -> None:
        return None

    def evaluate_intent(
        self,
        intent: OrderIntentPayload,
        *,
        runtime_positions: Iterable[PositionState] | None = None,
    ) -> RiskDecision:
        """Return an allow/block decision for a runtime order intent."""

        reason = self._validate_intent(intent)
        if reason is not None:
            return self._block_intent(intent, reason)

        positions = tuple(self._resolve_positions(runtime_positions))
        reason = self._check_runtime_state(intent, positions)
        if reason is not None:
            return self._block_intent(intent, reason)

        self._decision_counts["allowed"] += 1
        return RiskDecision(allowed=True)

    def set_kill_switch(self, enabled: bool) -> None:
        """Enable or disable the runtime kill switch."""

        self.kill_switch_enabled = enabled

    def set_entries_paused(self, enabled: bool) -> None:
        """Block non-reduce-only order intents while still allowing exits."""

        self.entries_paused = enabled

    def get_decision_counts(self) -> dict[str, int]:
        """Return a snapshot of allow/block counts."""

        return dict(self._decision_counts)

    async def submit_order_intent(
        self,
        intent: OrderIntentPayload,
        *,
        runtime_positions: Iterable[PositionState] | None = None,
    ) -> OrderSubmittedPayload | None:
        """Gate an intent and forward only approved requests to execution."""

        decision = self.evaluate_intent(intent, runtime_positions=runtime_positions)
        if not decision.allowed:
            return None
        if self.execution_service is None:
            raise RuntimeError("risk service is not connected to execution service")
        return await self.execution_service.submit_order_intent(intent)

    def _resolve_positions(
        self, runtime_positions: Iterable[PositionState] | None
    ) -> Iterable[PositionState]:
        if runtime_positions is not None:
            return runtime_positions
        source = self.position_source
        if source is None:
            return ()
        if hasattr(source, "get_all_position_states"):
            return source.get_all_position_states()  # type: ignore[attr-defined]
        if hasattr(source, "get_all_positions"):
            return source.get_all_positions()  # type: ignore[attr-defined]
        return ()

    def _check_runtime_state(
        self,
        intent: OrderIntentPayload,
        positions: tuple[PositionState, ...],
    ) -> str | None:
        if self.kill_switch_enabled:
            return "kill_switch_enabled"
        if self.entries_paused and not intent.reduce_only:
            return "entries_paused"
        if not intent.reduce_only and any(
            getattr(state, "status", None) == PositionStatus.RECONCILE_ERROR
            for state in positions
        ):
            return "reconciliation_required"
        gateway = getattr(self.execution_service, "user_stream_gateway", None)
        get_status = getattr(gateway, "get_status", None)
        if (
            not intent.reduce_only
            and callable(get_status)
            and get_status().get("connected") is False
        ):
            return "user_stream_disconnected"

        current_position = self._find_position_for_symbol(intent.symbol, positions)
        if current_position is not None and current_position.open_qty > 0:
            if not intent.reduce_only:
                return "duplicate_symbol_position"
            if intent.side == current_position.side:
                return "reduce_only_same_side"
            if intent.quantity > current_position.open_qty + 1e-12:
                return "reduce_only_quantity_exceeds_open_qty"

        if not intent.reduce_only and self.max_open_positions > 0:
            open_positions = sum(1 for state in positions if state.open_qty > 0)
            if current_position is None and open_positions >= self.max_open_positions:
                return "max_open_positions"

        return None

    def _validate_intent(self, intent: OrderIntentPayload) -> str | None:
        if not intent.symbol:
            return "missing_symbol"
        if intent.side not in {"buy", "sell"}:
            return "invalid_side"
        if intent.order_type not in {"market", "limit"}:
            return "invalid_order_type"
        if not isfinite(intent.quantity) or intent.quantity <= 0:
            return "invalid_quantity"
        if intent.limit_price is not None and (
            not isfinite(intent.limit_price) or intent.limit_price <= 0
        ):
            return "invalid_limit_price"
        if intent.stop_loss is not None and (
            not isfinite(intent.stop_loss) or intent.stop_loss <= 0
        ):
            return "invalid_stop_loss"
        if intent.take_profit is not None and (
            not isfinite(intent.take_profit) or intent.take_profit <= 0
        ):
            return "invalid_take_profit"
        if (
            intent.stop_loss is not None
            and intent.take_profit is not None
            and not self._is_stop_take_profit_sane(intent)
        ):
            return "invalid_stop_take_profit_combination"
        return None

    def _block_intent(self, intent: OrderIntentPayload, reason: str) -> RiskDecision:
        self._decision_counts["blocked"] += 1
        self._decision_counts[f"blocked.{reason}"] += 1
        self._emit_blocked_event(intent, reason)
        return RiskDecision(allowed=False, reason=reason)

    def _emit_blocked_event(self, intent: OrderIntentPayload, reason: str) -> None:
        self.event_bus.put_nowait(
            RuntimeEvent(
                meta=EventMeta(
                    event_id=f"risk-{intent.intent_id}",
                    event_type="risk.blocked",
                    source="risk.service",
                    symbol=intent.symbol,
                    ts_exchange=None,
                    ts_received=datetime.now(timezone.utc),
                ),
                payload=RiskBlockedPayload(
                    intent_id=intent.intent_id,
                    symbol=intent.symbol,
                    reason=reason,
                ),
            )
        )

    @staticmethod
    def _find_position_for_symbol(
        symbol: str, positions: tuple[PositionState, ...]
    ) -> PositionState | None:
        for state in positions:
            if state.symbol == symbol and state.status not in {
                PositionStatus.CLOSED,
                PositionStatus.RECONCILE_ERROR,
            }:
                return state
        return None

    @staticmethod
    def _is_stop_take_profit_sane(intent: OrderIntentPayload) -> bool:
        if intent.side == "buy":
            return intent.stop_loss < intent.take_profit
        if intent.side == "sell":
            return intent.stop_loss > intent.take_profit
        return False
