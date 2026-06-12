"""
WebSocket Manager for Binance Real-time Data Streaming

Provides low-latency market data via WebSocket connections instead of REST polling.
Supports both Spot and Futures markets.
"""

import asyncio
import logging
from typing import Dict, List, Callable, Optional, Any
from collections import defaultdict
from datetime import datetime

from binance import AsyncClient, BinanceSocketManager

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    Manages WebSocket connections to Binance for real-time data streaming.

    Features:
    - Auto-reconnection on disconnect
    - Multiple stream subscriptions
    - Callback-based event handling
    - Support for Spot and Futures markets
    """

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet

        self.client: Optional[AsyncClient] = None
        self.bm: Optional[BinanceSocketManager] = None

        # Active socket connections
        self.sockets: Dict[str, Any] = {}
        self.tasks: Dict[str, asyncio.Task] = {}

        # Callbacks for different event types
        self.callbacks: Dict[str, List[Callable]] = defaultdict(list)

        # Connection state
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10

        # Message queue for async processing
        self.message_queue: asyncio.Queue = asyncio.Queue()

    async def connect(self):
        """Initialize connection to Binance"""
        try:
            self.client = await AsyncClient.create(
                api_key=self.api_key, api_secret=self.api_secret, testnet=self.testnet
            )
            self.bm = BinanceSocketManager(self.client)
            self.is_connected = True
            self.reconnect_attempts = 0
            logger.info(f"WebSocket Manager connected (testnet={self.testnet})")

        except Exception as e:
            logger.error(f"Failed to connect WebSocket Manager: {e}")
            raise

    async def disconnect(self):
        """Close all connections and clean up"""
        try:
            # Cancel all running tasks
            for task in self.tasks.values():
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            self.tasks.clear()
            self.sockets.clear()

            if self.client:
                await self.client.close_connection()

            self.is_connected = False
            logger.info("WebSocket Manager disconnected")

        except Exception as e:
            logger.error(f"Error disconnecting WebSocket Manager: {e}")

    def register_callback(self, event_type: str, callback: Callable):
        """
        Register a callback for a specific event type.

        Event types:
        - 'kline': Candlestick/OHLCV updates
        - 'trade': Individual trades
        - 'ticker': 24hr ticker updates
        - 'depth': Order book updates
        - 'user': User data (orders, balances)
        """
        self.callbacks[event_type].append(callback)
        logger.debug(f"Registered callback for event type: {event_type}")

    async def _dispatch_event(self, event_type: str, data: Dict):
        """Dispatch event to all registered callbacks"""
        for callback in self.callbacks[event_type]:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)
            except Exception as e:
                logger.error(f"Error in callback for {event_type}: {e}")

    async def subscribe_kline(self, symbol: str, interval: str = "1m"):
        """
        Subscribe to candlestick/kline updates for a symbol.

        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')
            interval: Kline interval (1m, 5m, 15m, 1h, 4h, 1d, etc.)
        """
        stream_name = f"kline_{symbol}_{interval}"

        if stream_name in self.sockets:
            logger.warning(f"Already subscribed to {stream_name}")
            return

        async def handle_socket():
            socket = self.bm.kline_socket(symbol, interval=interval)
            async with socket as stream:
                self.sockets[stream_name] = socket
                logger.info(f"Subscribed to kline stream: {symbol}@{interval}")

                while True:
                    try:
                        msg = await stream.recv()

                        if msg.get("e") == "error":
                            logger.error(f"WebSocket error: {msg}")
                            break

                        # Parse kline data
                        kline = msg.get("k", {})
                        parsed_data = {
                            "symbol": symbol,
                            "interval": interval,
                            "open_time": kline.get("t"),
                            "close_time": kline.get("T"),
                            "open": float(kline.get("o", 0)),
                            "high": float(kline.get("h", 0)),
                            "low": float(kline.get("l", 0)),
                            "close": float(kline.get("c", 0)),
                            "volume": float(kline.get("v", 0)),
                            "is_closed": kline.get("x", False),
                            "timestamp": datetime.now(),
                        }

                        await self._dispatch_event("kline", parsed_data)

                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logger.error(f"Error in kline stream {stream_name}: {e}")
                        await asyncio.sleep(1)

        task = asyncio.create_task(handle_socket())
        self.tasks[stream_name] = task

    async def subscribe_trade(self, symbol: str):
        """Subscribe to real-time trade stream"""
        stream_name = f"trade_{symbol}"

        if stream_name in self.sockets:
            return

        async def handle_socket():
            socket = self.bm.trade_socket(symbol)
            async with socket as stream:
                self.sockets[stream_name] = socket
                logger.info(f"Subscribed to trade stream: {symbol}")

                while True:
                    try:
                        msg = await stream.recv()

                        parsed_data = {
                            "symbol": symbol,
                            "price": float(msg.get("p", 0)),
                            "quantity": float(msg.get("q", 0)),
                            "trade_time": msg.get("T"),
                            "is_buyer_maker": msg.get("m", False),
                            "timestamp": datetime.now(),
                        }

                        await self._dispatch_event("trade", parsed_data)

                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logger.error(f"Error in trade stream {stream_name}: {e}")
                        await asyncio.sleep(1)

        task = asyncio.create_task(handle_socket())
        self.tasks[stream_name] = task

    async def subscribe_ticker(self, symbol: str):
        """Subscribe to 24hr ticker updates"""
        stream_name = f"ticker_{symbol}"

        if stream_name in self.sockets:
            return

        async def handle_socket():
            socket = self.bm.symbol_ticker_socket(symbol)
            async with socket as stream:
                self.sockets[stream_name] = socket
                logger.info(f"Subscribed to ticker stream: {symbol}")

                while True:
                    try:
                        msg = await stream.recv()

                        parsed_data = {
                            "symbol": symbol,
                            "price": float(msg.get("c", 0)),
                            "price_change": float(msg.get("p", 0)),
                            "price_change_percent": float(msg.get("P", 0)),
                            "high": float(msg.get("h", 0)),
                            "low": float(msg.get("l", 0)),
                            "volume": float(msg.get("v", 0)),
                            "timestamp": datetime.now(),
                        }

                        await self._dispatch_event("ticker", parsed_data)

                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logger.error(f"Error in ticker stream {stream_name}: {e}")
                        await asyncio.sleep(1)

        task = asyncio.create_task(handle_socket())
        self.tasks[stream_name] = task

    async def subscribe_book_ticker(self, symbol: str):
        """Subscribe to best bid/ask updates."""
        stream_name = f"book_ticker_{symbol}"

        if stream_name in self.sockets:
            return

        async def handle_socket():
            socket = self.bm.symbol_book_ticker_socket(symbol)
            async with socket as stream:
                self.sockets[stream_name] = socket
                logger.info(f"Subscribed to book ticker stream: {symbol}")

                while True:
                    try:
                        msg = await stream.recv()

                        parsed_data = {
                            "symbol": symbol,
                            "bid_price": float(msg.get("b", 0)),
                            "bid_qty": float(msg.get("B", 0)),
                            "ask_price": float(msg.get("a", 0)),
                            "ask_qty": float(msg.get("A", 0)),
                            "timestamp": datetime.now(),
                        }

                        await self._dispatch_event("book_ticker", parsed_data)

                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logger.error(f"Error in book ticker stream {stream_name}: {e}")
                        await asyncio.sleep(1)

        task = asyncio.create_task(handle_socket())
        self.tasks[stream_name] = task

    async def subscribe_depth(self, symbol: str, depth: int = 10):
        """Subscribe to order book depth updates"""
        stream_name = f"depth_{symbol}"

        if stream_name in self.sockets:
            return

        async def handle_socket():
            socket = self.bm.depth_socket(symbol, depth=depth)
            async with socket as stream:
                self.sockets[stream_name] = socket
                logger.info(f"Subscribed to depth stream: {symbol}")

                while True:
                    try:
                        msg = await stream.recv()

                        parsed_data = {
                            "symbol": symbol,
                            "bids": [
                                (float(p), float(q)) for p, q in msg.get("bids", [])
                            ],
                            "asks": [
                                (float(p), float(q)) for p, q in msg.get("asks", [])
                            ],
                            "sequence": msg.get("lastUpdateId"),
                            "timestamp": datetime.now(),
                        }

                        await self._dispatch_event("depth", parsed_data)

                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logger.error(f"Error in depth stream {stream_name}: {e}")
                        await asyncio.sleep(1)

        task = asyncio.create_task(handle_socket())
        self.tasks[stream_name] = task

    async def subscribe_user_data(self):
        """Subscribe to user data stream (orders, balances)"""
        stream_name = "user_data"

        if stream_name in self.sockets:
            return

        async def handle_socket():
            socket = self.bm.user_socket()
            async with socket as stream:
                self.sockets[stream_name] = socket
                logger.info("Subscribed to user data stream")

                while True:
                    try:
                        msg = await stream.recv()

                        event_type = msg.get("e")

                        if event_type == "outboundAccountPosition":
                            # Balance update
                            parsed_data = {
                                "type": "balance_update",
                                "balances": {
                                    b["a"]: {
                                        "free": float(b["f"]),
                                        "locked": float(b["l"]),
                                    }
                                    for b in msg.get("B", [])
                                },
                                "timestamp": msg.get("E"),
                                "event_time": msg.get("E"),
                                "update_time": msg.get("u"),
                            }
                        elif event_type == "executionReport":
                            # Order update
                            parsed_data = {
                                "type": "order_update",
                                "symbol": msg.get("s"),
                                "client_order_id": msg.get("c"),
                                "order_id": msg.get("i"),
                                "side": msg.get("S"),
                                "order_type": msg.get("o"),
                                "status": msg.get("X"),
                                "price": float(msg.get("p", 0)),
                                "quantity": float(msg.get("q", 0)),
                                "filled_quantity": float(msg.get("z", 0)),
                                "last_fill_qty": float(msg.get("l", 0)),
                                "last_filled_quantity": float(msg.get("l", 0)),
                                "fill_price": float(msg.get("L", 0)),
                                "last_fill_price": float(msg.get("L", 0)),
                                "execution_type": msg.get("x"),
                                "cumulative_quote_qty": float(msg.get("Z", 0)),
                                "cumulative_quote_quantity": float(msg.get("Z", 0)),
                                "fee_cost": float(msg.get("n", 0)),
                                "fee_currency": msg.get("N"),
                                "is_maker": msg.get("m", False),
                                "reduce_only": msg.get("R", False),
                                "raw_status": msg.get("X"),
                                "event_time": msg.get("E"),
                                "trade_time": msg.get("T"),
                                "timestamp": msg.get("T") or msg.get("E"),
                            }
                        else:
                            parsed_data = msg

                        await self._dispatch_event("user", parsed_data)

                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logger.error(f"Error in user data stream: {e}")
                        await asyncio.sleep(1)

        task = asyncio.create_task(handle_socket())
        self.tasks[stream_name] = task

    async def subscribe_futures_kline(self, symbol: str, interval: str = "1m"):
        """Subscribe to futures kline stream"""
        stream_name = f"futures_kline_{symbol}_{interval}"

        if stream_name in self.sockets:
            return

        async def handle_socket():
            # Create futures socket manager
            socket = self.bm.kline_futures_socket(symbol, interval=interval)
            async with socket as stream:
                self.sockets[stream_name] = socket
                logger.info(f"Subscribed to futures kline stream: {symbol}@{interval}")

                while True:
                    try:
                        msg = await stream.recv()

                        kline = msg.get("k", {})
                        parsed_data = {
                            "symbol": symbol,
                            "market": "futures",
                            "interval": interval,
                            "open": float(kline.get("o", 0)),
                            "high": float(kline.get("h", 0)),
                            "low": float(kline.get("l", 0)),
                            "close": float(kline.get("c", 0)),
                            "volume": float(kline.get("v", 0)),
                            "is_closed": kline.get("x", False),
                            "timestamp": datetime.now(),
                        }

                        await self._dispatch_event("kline", parsed_data)

                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logger.error(f"Error in futures kline stream: {e}")
                        await asyncio.sleep(1)

        task = asyncio.create_task(handle_socket())
        self.tasks[stream_name] = task

    def get_active_streams(self) -> List[str]:
        """Get list of active stream names"""
        return list(self.sockets.keys())

    async def unsubscribe(self, stream_name: str):
        """Unsubscribe from a specific stream"""
        if stream_name in self.tasks:
            self.tasks[stream_name].cancel()
            try:
                await self.tasks[stream_name]
            except asyncio.CancelledError:
                pass
            del self.tasks[stream_name]

        if stream_name in self.sockets:
            del self.sockets[stream_name]

        logger.info(f"Unsubscribed from stream: {stream_name}")
