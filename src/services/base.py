"""Base service abstractions for the runtime shell."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@runtime_checkable
class AsyncService(Protocol):
    """Minimal async lifecycle contract for runtime services."""

    @property
    def name(self) -> str: ...

    @property
    def is_running(self) -> bool: ...

    @property
    def logger(self) -> logging.Logger: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


@dataclass(slots=True)
class BaseService(ABC):
    """Reusable base class that standardizes service lifecycle semantics."""

    name: str
    logger: logging.Logger = field(init=False)
    is_running: bool = field(default=False, init=False)
    _is_starting: bool = field(default=False, init=False, repr=False)
    _is_stopping: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("service name must not be empty")
        self.logger = logging.getLogger(f"akiba.runtime.service.{self.name}")

    async def start(self) -> None:
        """Start the service once and mark it running."""

        if self.is_running or self._is_starting:
            return

        self._is_starting = True
        try:
            await self.on_start()
            self.is_running = True
        finally:
            self._is_starting = False

    async def stop(self) -> None:
        """Stop the service once and clear the running flag."""

        if not self.is_running or self._is_stopping:
            return

        self._is_stopping = True
        try:
            await self.on_stop()
        finally:
            self.is_running = False
            self._is_stopping = False

    @abstractmethod
    async def on_start(self) -> None:
        """Hook implemented by concrete services."""

    @abstractmethod
    async def on_stop(self) -> None:
        """Hook implemented by concrete services."""
