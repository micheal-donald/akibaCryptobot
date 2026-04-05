"""Native execution port contracts for the hot-path boundary."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .exchange import ExecutionGateway


@runtime_checkable
class NativeExecutionGateway(ExecutionGateway, Protocol):
    """Native hot-path contract for execution and order-state updates."""

    pass
