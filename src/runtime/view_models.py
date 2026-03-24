"""Read-only runtime projection models for operator-facing surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ProjectionSource(str, Enum):
    """Marks which subsystem is authoritative for the projected view."""

    LEGACY = "legacy"
    SHADOW = "shadow"
    RUNTIME_PROJECTION = "runtime_projection"


@dataclass(slots=True, frozen=True)
class ProjectedPositionView:
    """Stable read model for a runtime-managed position."""

    position_id: str
    symbol: str
    side: str
    status: str
    open_qty: float
    requested_qty: float
    avg_entry_price: float | None
    avg_exit_price: float | None
    stop_loss: float | None
    take_profit: float | None
    trailing_stop: float | None
    entry_order_ids: tuple[str, ...]
    protective_order_ids: tuple[str, ...]
    fees_paid: float
    opened_at: datetime | None
    closed_at: datetime | None
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class ProjectedOrderView:
    """Stable read model for a runtime-managed order."""

    client_order_id: str
    exchange_order_id: str | None
    symbol: str
    side: str
    order_type: str
    status: str
    requested_qty: float
    filled_qty: float
    avg_fill_price: float | None
    reduce_only: bool
    parent_position_id: str | None
    requested_price: float | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class RuntimeProjection:
    """Top-level projection consumed by operator-facing surfaces."""

    source: ProjectionSource
    runtime_mode: str
    runtime_enabled: bool
    runtime_is_running: bool
    runtime_is_healthy: bool
    runtime_owns_entries: bool
    runtime_owns_exits: bool
    active_position_count: int
    protected_position_count: int
    pending_exit_count: int
    open_order_count: int
    rejected_order_count: int
    positions: tuple[ProjectedPositionView, ...]
    orders: tuple[ProjectedOrderView, ...]


__all__ = [
    "ProjectedOrderView",
    "ProjectedPositionView",
    "ProjectionSource",
    "RuntimeProjection",
]
