"""Bundle contract for optional native hot-path ports."""

from __future__ import annotations

from dataclasses import dataclass

from .native_execution import NativeExecutionGateway
from .native_market_data import NativeMarketDataGateway


@dataclass(slots=True, frozen=True)
class NativeHotPathPorts:
    """Optional native hot-path provider bundle."""

    market_data: NativeMarketDataGateway | None = None
    execution: NativeExecutionGateway | None = None
