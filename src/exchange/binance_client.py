import ccxt
import os
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
import pandas as pd
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class BinanceClient:
    def __init__(self, api_key: str = None, secret_key: str = None, sandbox: bool = True):
        self.api_key = api_key or os.getenv('BINANCE_API_KEY')
        self.secret_key = secret_key or os.getenv('BINANCE_SECRET_KEY')
        self.sandbox = sandbox
        
        self.exchange = ccxt.binance({
            'apiKey': self.api_key,
            'secret': self.secret_key,
            'sandbox': self.sandbox,
            'enableRateLimit': True,
            'rateLimit': 1200,
            'options': {
                'adjustForTimeDifference': True,
                'warnOnFetchOpenOrdersWithoutSymbol': False,
            }
        })
        
        if self.sandbox:
            self.exchange.set_sandbox_mode(True)
            
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

    async def get_ohlcv(self, symbol: str, timeframe: str = '1h', limit: int = 100) -> pd.DataFrame:
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
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

    async def place_limit_order(self, symbol: str, side: str, amount: float, price: float) -> Dict:
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

    def calculate_position_size(self, balance: float, risk_percent: float, entry_price: float, stop_loss_price: float) -> float:
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
        if hasattr(self.exchange, 'close'):
            await self.exchange.close()