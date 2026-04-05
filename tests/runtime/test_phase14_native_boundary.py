from __future__ import annotations

from dataclasses import dataclass

from adapters.binance.execution_gateway import BinanceExecutionGateway
from adapters.binance.ws_gateway import BinanceWebSocketGateway
from ports import NativeExecutionGateway, NativeHotPathPorts, NativeMarketDataGateway
from runtime.app import RuntimeApp
from runtime.config import RuntimeConfig


@dataclass(slots=True)
class _FakeWebSocketManager:
    callbacks: dict[str, object]
    is_connected: bool = False

    async def connect(self) -> None:
        self.is_connected = True

    async def disconnect(self) -> None:
        self.is_connected = False

    def register_callback(self, event_type: str, callback: object) -> None:
        self.callbacks[event_type] = callback

    async def subscribe_book_ticker(self, symbol: str) -> None:
        return None

    async def subscribe_kline(self, symbol: str, interval: str = "1m") -> None:
        return None


@dataclass(slots=True)
class _FakeNativeExchange:
    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "symbol": symbol,
            "type": type,
            "side": side,
            "amount": amount,
            "price": price,
            "params": params or {},
        }


@dataclass(slots=True)
class _FakeExecutionClient:
    exchange: _FakeNativeExchange

    async def ensure_markets_loaded(self) -> None:
        return None

    async def place_market_order(
        self, symbol: str, side: str, amount: float, price: float | None = None
    ) -> dict[str, object]:
        return {
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": price,
        }

    async def place_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> dict[str, object]:
        return {
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": price,
        }

    async def cancel_order(self, order_id: str, symbol: str) -> dict[str, object]:
        return {"order_id": order_id, "symbol": symbol, "status": "cancelled"}

    async def get_order_status(self, order_id: str, symbol: str) -> dict[str, object]:
        return {"order_id": order_id, "symbol": symbol, "status": "filled"}

    async def close(self) -> None:
        return None


def _runtime_config() -> RuntimeConfig:
    return RuntimeConfig.from_mapping(
        {
            "trading": {
                "pairs": ["BTC/USDT"],
                "timeframes": {"primary": "1m"},
            },
            "exchange": {"sandbox": True},
        }
    )


def test_native_ports_are_exported_and_structural() -> None:
    native_ports = NativeHotPathPorts()

    assert native_ports.market_data is None
    assert native_ports.execution is None


def test_python_gateways_satisfy_native_hot_path_protocols() -> None:
    market_data_gateway = BinanceWebSocketGateway(
        websocket_manager=_FakeWebSocketManager(callbacks={}),
        api_key="",
        api_secret="",
        testnet=True,
    )
    execution_gateway = BinanceExecutionGateway(
        client=_FakeExecutionClient(exchange=_FakeNativeExchange()),
    )

    assert isinstance(market_data_gateway, NativeMarketDataGateway)
    assert isinstance(execution_gateway, NativeExecutionGateway)


def test_runtime_app_can_boot_with_native_hot_path_bundle() -> None:
    config = _runtime_config()
    app = RuntimeApp.build(
        config,
        native_hot_path_ports=NativeHotPathPorts(),
    )

    assert app.config == config
    assert app.native_hot_path_ports == NativeHotPathPorts()
