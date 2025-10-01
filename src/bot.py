import asyncio
import json
import logging
import os
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import signal
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from exchange.binance_client import BinanceClient
from bot_logging.logger import setup_logging
from risk_management import RiskManager
from notifications.telegram_bot import TelegramNotifier
from strategies.momentum_strategy import MomentumStrategy
from strategies.base_strategy import StrategyManager


class AkibaCryptoBot:
    def __init__(self, config_path: str = "config/config.json"):
        self.config = self.load_config(config_path)
        self.logger = setup_logging(self.config.get('logging', {}))
        
        self.exchange = BinanceClient(
            sandbox=self.config['exchange'].get('sandbox', True)
        )
        
        self.risk_manager = RiskManager(self.config.get('risk_management', {}))
        self.telegram = TelegramNotifier() if self.config.get('notifications', {}).get('telegram_enabled') else None
        
        # Setup Telegram bot commands if enabled
        if self.telegram and self.telegram.enabled:
            self.telegram.setup_bot_commands(self)
        
        # Initialize strategy manager
        self.strategy_manager = StrategyManager(self.config)
        
        # Load strategies based on configuration
        self._load_strategies()
        
        self.active_trades = {}
        self.is_running = False
        self.last_update = datetime.now()
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def load_config(self, config_path: str) -> Dict:
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            return config
        except FileNotFoundError:
            self.logger.error(f"Config file not found: {config_path}")
            raise
        except json.JSONDecodeError:
            self.logger.error(f"Invalid JSON in config file: {config_path}")
            raise

    def signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.stop()

    async def start(self):
        self.logger.info("Starting AkibaCryptoBot...")
        self.is_running = True
        
        try:
            # Initialize exchange connection
            await self.exchange.get_balance()
            self.logger.info("Successfully connected to exchange")
            
            if self.telegram:
                await self.telegram.send_message("🤖 AkibaCryptoBot started successfully!")
                # Start Telegram bot polling in background
                if self.telegram.application:
                    await self.telegram.application.initialize()
                    asyncio.create_task(self.telegram.application.start())
                    asyncio.create_task(self.telegram.application.updater.start_polling())
            
            # Main trading loop
            while self.is_running:
                try:
                    await self.trading_cycle()
                    await asyncio.sleep(60)  # Run every minute
                except Exception as e:
                    self.logger.error(f"Error in trading cycle: {e}")
                    if self.telegram:
                        await self.telegram.send_message(f"❌ Trading cycle error: {str(e)}")
                    await asyncio.sleep(60)
                    
        except Exception as e:
            self.logger.error(f"Critical error: {e}")
            if self.telegram:
                await self.telegram.send_message(f"🚨 Critical error: {str(e)}")
        finally:
            await self.cleanup()

    async def trading_cycle(self):
        self.logger.debug("Starting trading cycle...")
        
        # Update market data
        await self.update_market_data()
        
        # Check existing positions
        await self.manage_positions()
        
        # Look for new trading opportunities
        await self.scan_opportunities()
        
        # Update last cycle time
        self.last_update = datetime.now()

    async def update_market_data(self):
        for pair in self.config['trading']['pairs']:
            try:
                ticker = await self.exchange.get_ticker(pair)
                # Store or process ticker data as needed
                self.logger.debug(f"Updated {pair}: {ticker['last']}")
            except Exception as e:
                self.logger.error(f"Error updating market data for {pair}: {e}")

    async def manage_positions(self):
        """Manage existing positions and orders"""
        try:
            # Check each active trade individually to avoid rate limits
            for symbol in list(self.active_trades.keys()):
                try:
                    # Get open orders for specific symbol
                    open_orders = await self.exchange.get_open_orders(symbol)
                    
                    for order in open_orders:
                        await self.check_order_conditions(order)
                        
                    # Also check current position for exit conditions
                    ticker = await self.exchange.get_ticker(symbol)
                    current_price = ticker['last']
                    
                    # Get OHLCV data for exit signal analysis
                    ohlcv_data = await self.exchange.get_ohlcv(symbol, 
                        self.config['trading']['timeframes']['primary'], 50)
                    
                    # Check if we should exit the position
                    exit_signal = await self.strategy_manager.get_active_strategy().should_exit_position(
                        symbol, self.active_trades[symbol], current_price, ohlcv_data
                    )
                    
                    if exit_signal:
                        await self.close_position(symbol, exit_signal.metadata.get('exit_reason', 'strategy_exit'))
                        
                except Exception as e:
                    self.logger.error(f"Error managing position for {symbol}: {e}")
                    
        except Exception as e:
            self.logger.error(f"Error in manage_positions: {e}")

    async def check_order_conditions(self, order: Dict):
        symbol = order['symbol']
        
        # Get current price
        ticker = await self.exchange.get_ticker(symbol)
        current_price = ticker['last']
        
        # Implement stop-loss and take-profit logic
        if symbol in self.active_trades:
            trade = self.active_trades[symbol]
            
            # Check stop-loss
            if self.should_stop_loss(trade, current_price):
                await self.close_position(symbol, "stop_loss")
                
            # Check take-profit
            elif self.should_take_profit(trade, current_price):
                await self.close_position(symbol, "take_profit")

    def should_stop_loss(self, trade: Dict, current_price: float) -> bool:
        if trade['side'] == 'buy':
            return current_price <= trade['stop_loss']
        else:
            return current_price >= trade['stop_loss']

    def should_take_profit(self, trade: Dict, current_price: float) -> bool:
        if trade['side'] == 'buy':
            return current_price >= trade['take_profit']
        else:
            return current_price <= trade['take_profit']

    async def close_position(self, symbol: str, reason: str):
        try:
            if symbol in self.active_trades:
                trade = self.active_trades[symbol]
                
                # Close the position
                side = 'sell' if trade['side'] == 'buy' else 'buy'
                order = await self.exchange.place_market_order(
                    symbol, side, trade['amount']
                )
                
                self.logger.info(f"Closed position {symbol} due to {reason}")
                
                if self.telegram:
                    await self.telegram.send_message(
                        f"📊 Closed {symbol} position\n"
                        f"Reason: {reason}\n"
                        f"Order ID: {order['id']}"
                    )
                
                # Remove from active trades
                del self.active_trades[symbol]
                
        except Exception as e:
            self.logger.error(f"Error closing position {symbol}: {e}")

    async def scan_opportunities(self):
        """Scan for trading opportunities using the active strategy"""
        try:
            for pair in self.config['trading']['pairs']:
                # Skip if we already have an active position
                if pair in self.active_trades:
                    continue
                
                # Check if we're at max open trades
                if len(self.active_trades) >= self.config['trading'].get('max_open_trades', 3):
                    self.logger.debug("Maximum open trades reached, skipping new opportunities")
                    break
                
                # Get market data
                try:
                    ohlcv_data = await self.exchange.get_ohlcv(
                        pair, 
                        self.config['trading']['timeframes']['primary'],
                        200  # Get enough history for indicators
                    )
                    
                    if ohlcv_data is None or len(ohlcv_data) < 50:
                        self.logger.warning(f"Insufficient data for {pair}")
                        continue
                    
                    ticker = await self.exchange.get_ticker(pair)
                    current_price = ticker['last']
                    
                    # Generate trading signal
                    signal = await self.strategy_manager.generate_signal(pair, ohlcv_data, current_price)
                    
                    if signal and self.validate_trading_conditions(pair, signal):
                        # Calculate position size
                        balance = await self.exchange.get_balance()
                        usdt_balance = balance.get('USDT', {}).get('free', 0)
                        
                        if usdt_balance < 10:  # Minimum balance check
                            self.logger.warning(f"Insufficient balance: {usdt_balance} USDT")
                            continue
                        
                        # Calculate position size using risk management
                        position_size = self.risk_manager.calculate_position_size_with_signal(
                            usdt_balance, signal.strength, current_price, signal.stop_loss
                        )
                        
                        if position_size > 0:
                            await self.place_trade(pair, signal.signal_type.value, position_size, current_price)
                        
                except Exception as e:
                    self.logger.error(f"Error processing {pair}: {e}")
                    continue
                    
        except Exception as e:
            self.logger.error(f"Error in scan_opportunities: {e}")

    def validate_trading_conditions(self, symbol: str, signal) -> bool:
        """Validate if trading conditions are met"""
        try:
            # Check risk management limits
            if not self.risk_manager.can_open_position():
                self.logger.debug(f"Risk manager blocked new position for {symbol}")
                return False
            
            # Check if signal meets minimum strength
            if signal.strength < self.config.get('min_signal_strength', 0.6):
                self.logger.debug(f"Signal strength {signal.strength:.2f} below threshold for {symbol}")
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error validating trading conditions for {symbol}: {e}")
            return False

    async def place_trade(self, symbol: str, side: str, amount: float, entry_price: float):
        try:
            # Calculate stop-loss and take-profit
            stop_loss = self.risk_manager.calculate_stop_loss(entry_price, side)
            take_profit = self.risk_manager.calculate_take_profit(entry_price, side)
            
            # Place the order
            order = await self.exchange.place_market_order(symbol, side, amount)
            
            # Track the trade
            self.active_trades[symbol] = {
                'side': side,
                'amount': amount,
                'entry_price': entry_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'timestamp': datetime.now(),
                'order_id': order['id']
            }
            
            self.logger.info(f"Placed {side} order for {amount} {symbol}")
            
            if self.telegram:
                await self.telegram.send_message(
                    f"📈 New {side.upper()} trade\n"
                    f"Symbol: {symbol}\n"
                    f"Amount: {amount}\n"
                    f"Entry: ${entry_price:.4f}\n"
                    f"Stop Loss: ${stop_loss:.4f}\n"
                    f"Take Profit: ${take_profit:.4f}"
                )
                
        except Exception as e:
            self.logger.error(f"Error placing trade: {e}")

    def stop(self):
        self.logger.info("Stopping bot...")
        self.is_running = False

    async def cleanup(self):
        self.logger.info("Cleaning up resources...")
        
        if self.exchange:
            await self.exchange.close()
            
        if self.telegram:
            try:
                await self.telegram.send_message("🛑 AkibaCryptoBot stopped")
                if self.telegram.application and self.telegram.application.running:
                    await self.telegram.application.stop()
                    await self.telegram.application.shutdown()
            except Exception as e:
                self.logger.error(f"Error stopping Telegram bot: {e}")

    # Methods for Telegram bot integration
    async def get_status_info(self) -> Dict:
        """Get bot status information for Telegram commands"""
        uptime = datetime.now() - self.last_update if hasattr(self, 'start_time') else timedelta(0)
        return {
            'active_trades': len(self.active_trades),
            'daily_pnl': 0.0,  # Calculate actual PnL if needed
            'uptime': str(uptime),
            'is_running': self.is_running
        }

    async def get_balance_info(self) -> Dict:
        """Get account balance for Telegram commands"""
        try:
            balance = await self.exchange.get_balance()
            return {currency: data['free'] for currency, data in balance.items() if data['free'] > 0}
        except Exception as e:
            self.logger.error(f"Error getting balance: {e}")
            return {}

    async def get_active_trades(self) -> Dict:
        """Get active trades for Telegram commands"""
        return self.active_trades

    async def get_config_info(self) -> Dict:
        """Get configuration info for Telegram commands"""
        return {
            'trading_mode': 'paper' if self.config['exchange'].get('sandbox', True) else 'live',
            'max_position_size': self.config['risk_management'].get('max_position_size', 0.05),
            'stop_loss_percent': self.config['risk_management'].get('stop_loss_percent', 0.02),
            'take_profit_percent': self.config['risk_management'].get('take_profit_percent', 0.04),
            'max_open_trades': self.config['trading'].get('max_open_trades', 3),
            'pairs': self.config['trading']['pairs']
        }

    def _load_strategies(self):
        """Load strategies based on configuration"""
        try:
            # Load default momentum strategy
            momentum_strategy = MomentumStrategy(self.config.get('strategy_config', {}))
            self.strategy_manager.add_strategy(momentum_strategy, 'momentum')
            
            # Load strategy specified in config
            config_strategy = self.config['trading'].get('strategy', 'momentum')
            if config_strategy and config_strategy != 'momentum':
                self.strategy_manager.add_strategy_by_name(config_strategy, self.config)
                self.strategy_manager.set_active_strategy(config_strategy)
            else:
                self.strategy_manager.set_active_strategy('momentum')
                
            self.logger.info(f"Active strategy: {self.strategy_manager.active_strategy}")
            
        except Exception as e:
            self.logger.error(f"Error loading strategies: {e}")

    async def pause_trading(self):
        """Pause trading (called by Telegram /stop command)"""
        self.is_running = False
        self.logger.info("Trading paused via Telegram command")

    async def resume_trading(self):
        """Resume trading (called by Telegram /resume command)"""
        self.is_running = True
        self.logger.info("Trading resumed via Telegram command")


async def main():
    bot = AkibaCryptoBot()
    try:
        await bot.start()
    except KeyboardInterrupt:
        bot.logger.info("Received keyboard interrupt")
    except Exception as e:
        bot.logger.error(f"Unexpected error: {e}")
    finally:
        bot.stop()


if __name__ == "__main__":
    asyncio.run(main())