"""Execution and position lifecycle state models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class OrderStatus(StrEnum):
    """Canonical order lifecycle states for the runtime."""

    CREATED = "created"
    SUBMIT_PENDING = "submit_pending"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ORPHANED = "orphaned"


class PositionStatus(StrEnum):
    """Canonical position lifecycle states for the runtime."""

    NEW = "new"
    ENTRY_PENDING = "entry_pending"
    OPEN = "open"
    PROTECTIVE_PENDING = "protective_pending"
    PROTECTED = "protected"
    REDUCE_PENDING = "reduce_pending"
    CLOSED = "closed"
    RECONCILE_ERROR = "reconcile_error"


@dataclass(slots=True)
class OrderState:
    """Mutable runtime state for a single exchange order."""

    client_order_id: str
    symbol: str
    side: str
    order_type: str
    requested_qty: float
    status: OrderStatus = OrderStatus.CREATED
    order_id: str | None = None
    filled_qty: float = 0.0
    avg_fill_price: float | None = None
    reduce_only: bool = False
    parent_position_id: str | None = None
    requested_price: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class PositionState:
    """Mutable runtime state for a single managed position."""

    position_id: str
    symbol: str
    side: str
    requested_qty: float
    status: PositionStatus = PositionStatus.NEW
    open_qty: float = 0.0
    avg_entry_price: float | None = None
    avg_exit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    trailing_stop: float | None = None
    entry_order_ids: tuple[str, ...] = ()
    protective_order_ids: tuple[str, ...] = ()
    fees_paid: float = 0.0
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
