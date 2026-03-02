import ccxt
import os
from typing import Dict, List, Optional, Tuple, Callable
from decimal import Decimal
import pandas as pd
from datetime import datetime
import logging
import asyncio

logger = logging.getLogger(__name__)


class BinanceClient:
    """
    Binance exchange client supporting both REST API and WebSocket streaming.

    Supports:
    - Spot market trading
    - Futures market trading (USDT-M)
    - Real-time WebSocket data streaming
    """

    def __init__(
        self,
        api_key: str = None,
        secret_key: str = None,
        sandbox: bool = True,
        market_type: str = "spot",  # 'spot' or 'futures'
    ):
        self.api_key = api_key if api_key is not None else os.getenv("BINANCE_API_KEY")
        self.secret_key = (
            secret_key if secret_key is not None else os.getenv("BINANCE_SECRET_KEY")
        )
        self.sandbox = sandbox
        self.market_type = market_type

        # Initialize ccxt exchange for REST API
        exchange_class = ccxt.binance if market_type == "spot" else ccxt.binanceusdm
        ccxt_config = {
            "sandbox": self.sandbox,
            "enableRateLimit": True,
            "rateLimit": 1200,
            "options": {
                "adjustForTimeDifference": True,
                "warnOnFetchOpenOrdersWithoutSymbol": False,
                "defaultType": market_type,
            },
        }
        if self.api_key:
            ccxt_config["apiKey"] = self.api_key
        if self.secret_key:
            ccxt_config["secret"] = self.secret_key
        self.exchange = exchange_class(ccxt_config)

        if self.sandbox:
            self.exchange.set_sandbox_mode(True)

        # WebSocket manager (lazy initialized)
        self._ws_manager = None
        self._ws_callbacks: Dict[str, List[Callable]] = {}

        # Real-time data cache
        self.realtime_prices: Dict[str, float] = {}
        self.realtime_klines: Dict[str, Dict] = {}

    async def get_balance(self) -> Dict:
        try:
            balance = self.exchange.fetch_balance()
            return balance
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            raise

    async def get_ticker(self, symbol: str) -> Dict:
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker
        except Exception as e:
            logger.error(f"Error fetching ticker for {symbol}: {e}")
            raise

    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> pd.DataFrame:
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(
                ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            return df
        except Exception as e:
            logger.error(f"Error fetching OHLCV data for {symbol}: {e}")
            raise

    async def place_market_order(self, symbol: str, side: str, amount: float) -> Dict:
        try:
            order = self.exchange.create_market_order(symbol, side, amount)
            logger.info(f"Market order placed: {side} {amount} {symbol}")
            return order
        except Exception as e:
            logger.error(f"Error placing market order: {e}")
            raise

    async def place_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> Dict:
        try:
            order = self.exchange.create_limit_order(symbol, side, amount, price)
            logger.info(f"Limit order placed: {side} {amount} {symbol} at {price}")
            return order
        except Exception as e:
            logger.error(f"Error placing limit order: {e}")
            raise

    async def cancel_order(self, order_id: str, symbol: str) -> Dict:
        try:
            result = self.exchange.cancel_order(order_id, symbol)
            logger.info(f"Order {order_id} cancelled for {symbol}")
            return result
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            raise

    async def get_open_orders(self, symbol: str = None) -> List[Dict]:
        try:
            orders = self.exchange.fetch_open_orders(symbol)
            return orders
        except Exception as e:
            logger.error(f"Error fetching open orders: {e}")
            raise

    async def get_order_status(self, order_id: str, symbol: str) -> Dict:
        try:
            order = self.exchange.fetch_order(order_id, symbol)
            return order
        except Exception as e:
            logger.error(f"Error fetching order status: {e}")
            raise

    async def get_trading_fees(self, symbol: str = None) -> Dict:
        try:
            fees = self.exchange.fetch_trading_fees()
            if symbol:
                return fees.get(symbol, {})
            return fees
        except Exception as e:
            logger.error(f"Error fetching trading fees: {e}")
            raise

    def calculate_position_size(
        self,
        balance: float,
        risk_percent: float,
        entry_price: float,
        stop_loss_price: float,
    ) -> float:
        risk_amount = balance * risk_percent
        price_diff = abs(entry_price - stop_loss_price)
        if price_diff == 0:
            return 0
        position_size = risk_amount / price_diff
        return position_size

    async def get_market_depth(self, symbol: str, limit: int = 100) -> Dict:
        try:
            orderbook = self.exchange.fetch_order_book(symbol, limit)
            return orderbook
        except Exception as e:
            logger.error(f"Error fetching market depth for {symbol}: {e}")
            raise

    async def close(self):
        """Close all connections"""
        if self._ws_manager:
            await self._ws_manager.disconnect()
        if hasattr(self.exchange, "close"):
            await self.exchange.close()

    # ============== WebSocket Methods ==============

    async def init_websocket(self):
        """Initialize WebSocket manager for real-time data"""
        from exchange.websocket_manager import WebSocketManager

        if self._ws_manager is None:
            self._ws_manager = WebSocketManager(
                api_key=self.api_key, api_secret=self.secret_key, testnet=self.sandbox
            )
            await self._ws_manager.connect()

            # Register internal price update handler
            self._ws_manager.register_callback("ticker", self._on_ticker_update)
            self._ws_manager.register_callback("kline", self._on_kline_update)

        return self._ws_manager

    async def _on_ticker_update(self, data: Dict):
        """Internal handler to cache real-time prices"""
        symbol = data.get("symbol")
        if symbol:
            self.realtime_prices[symbol] = data.get("price", 0)

    async def _on_kline_update(self, data: Dict):
        """Internal handler to cache real-time klines"""
        symbol = data.get("symbol")
        if symbol:
            self.realtime_klines[symbol] = data

    async def subscribe_realtime(self, symbols: List[str], streams: List[str] = None):
        """
        Subscribe to real-time data streams for multiple symbols.

        Args:
            symbols: List of trading pairs (e.g., ['BTCUSDT', 'ETHUSDT'])
            streams: List of stream types ('kline', 'trade', 'ticker', 'depth')
        """
        if not self._ws_manager:
            await self.init_websocket()

        streams = streams or ["ticker", "kline"]

        for symbol in symbols:
            for stream in streams:
                if stream == "kline":
                    if self.market_type == "futures":
                        await self._ws_manager.subscribe_futures_kline(symbol)
                    else:
                        await self._ws_manager.subscribe_kline(symbol)
                elif stream == "ticker":
                    await self._ws_manager.subscribe_ticker(symbol)
                elif stream == "trade":
                    await self._ws_manager.subscribe_trade(symbol)
                elif stream == "depth":
                    await self._ws_manager.subscribe_depth(symbol)

        logger.info(f"Subscribed to {streams} for {len(symbols)} symbols")

    def get_realtime_price(self, symbol: str) -> Optional[float]:
        """Get cached real-time price for a symbol"""
        return self.realtime_prices.get(symbol)

    def on_price_update(self, callback: Callable):
        """Register callback for real-time price updates"""
        if self._ws_manager:
            self._ws_manager.register_callback("ticker", callback)

    def on_kline_update(self, callback: Callable):
        """Register callback for real-time kline updates"""
        if self._ws_manager:
            self._ws_manager.register_callback("kline", callback)

    # ============== Futures-Specific Methods ==============

    async def set_leverage(self, symbol: str, leverage: int) -> Dict:
        """Set leverage for a futures symbol (Futures only)"""
        if self.market_type != "futures":
            raise ValueError("set_leverage only available for futures markets")

        try:
            result = self.exchange.set_leverage(leverage, symbol)
            logger.info(f"Set leverage to {leverage}x for {symbol}")
            return result
        except Exception as e:
            logger.error(f"Error setting leverage for {symbol}: {e}")
            raise

    async def set_margin_mode(self, symbol: str, margin_type: str = "isolated") -> Dict:
        """Set margin mode for a futures symbol (Futures only)"""
        if self.market_type != "futures":
            raise ValueError("set_margin_mode only available for futures markets")

        try:
            result = self.exchange.set_margin_mode(margin_type, symbol)
            logger.info(f"Set margin mode to {margin_type} for {symbol}")
            return result
        except Exception as e:
            logger.error(f"Error setting margin mode for {symbol}: {e}")
            raise

    async def get_futures_positions(self, symbol: str = None) -> List[Dict]:
        """Get open futures positions (Futures only)"""
        if self.market_type != "futures":
            raise ValueError("get_futures_positions only available for futures markets")

        try:
            positions = self.exchange.fetch_positions(
                symbols=[symbol] if symbol else None
            )
            # Filter for non-zero positions
            return [p for p in positions if float(p.get("contracts", 0)) > 0]
        except Exception as e:
            logger.error(f"Error fetching futures positions: {e}")
            raise

    async def get_funding_rate(self, symbol: str) -> Dict:
        """Get current funding rate for a futures symbol"""
        if self.market_type != "futures":
            raise ValueError("get_funding_rate only available for futures markets")

        try:
            funding = self.exchange.fetch_funding_rate(symbol)
            return funding
        except Exception as e:
            logger.error(f"Error fetching funding rate for {symbol}: {e}")
            raise
