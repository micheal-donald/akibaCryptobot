import asyncio
import os
from typing import Optional, Dict, List
import logging
from datetime import datetime
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
import json

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token or os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = chat_id or os.getenv('TELEGRAM_CHAT_ID')
        
        if not self.bot_token:
            logger.warning("Telegram bot token not provided, notifications disabled")
            self.enabled = False
            return
        
        if not self.chat_id:
            logger.warning("Telegram chat ID not provided, notifications disabled")
            self.enabled = False
            return
        
        self.enabled = True
        self.bot = Bot(token=self.bot_token)
        self.application = None
        
        # Bot state for commands
        self.bot_status = "running"
        self.trade_notifications = True
        self.error_notifications = True
        
        logger.info("Telegram notifier initialized")

    async def send_message(self, message: str, parse_mode: str = None) -> bool:
        """Send a message to the configured chat"""
        if not self.enabled:
            return False
        
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=parse_mode
            )
            logger.debug(f"Telegram message sent: {message[:50]}...")
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def send_trade_notification(self, trade_data: Dict):
        """Send trade-specific notification"""
        if not self.enabled or not self.trade_notifications:
            return
        
        action = trade_data.get('action', 'UNKNOWN')
        symbol = trade_data.get('symbol', 'UNKNOWN')
        side = trade_data.get('side', '').upper()
        amount = trade_data.get('amount', 0)
        price = trade_data.get('price', 0)
        
        if action == 'entry':
            message = (
                f"📈 NEW {side} TRADE\n"
                f"Symbol: {symbol}\n"
                f"Amount: {amount}\n"
                f"Entry Price: ${price:.6f}\n"
                f"Time: {datetime.now().strftime('%H:%M:%S')}"
            )
            
            if 'stop_loss' in trade_data:
                message += f"\nStop Loss: ${trade_data['stop_loss']:.6f}"
            if 'take_profit' in trade_data:
                message += f"\nTake Profit: ${trade_data['take_profit']:.6f}"
                
        elif action == 'exit':
            pnl = trade_data.get('pnl', 0)
            reason = trade_data.get('reason', 'manual')
            pnl_emoji = "📈" if pnl > 0 else "📉"
            
            message = (
                f"{pnl_emoji} CLOSED {side} TRADE\n"
                f"Symbol: {symbol}\n"
                f"Exit Price: ${price:.6f}\n"
                f"PnL: ${pnl:.6f}\n"
                f"Reason: {reason}\n"
                f"Time: {datetime.now().strftime('%H:%M:%S')}"
            )
            
        else:
            message = f"🔄 Trade Update: {symbol} - {action}"
        
        await self.send_message(message)

    async def send_error_notification(self, error: str, context: str = ""):
        """Send error notification"""
        if not self.enabled or not self.error_notifications:
            return
        
        message = (
            f"🚨 ERROR ALERT\n"
            f"Context: {context}\n"
            f"Error: {error}\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await self.send_message(message)

    async def send_daily_report(self, report_data: Dict):
        """Send daily performance report"""
        if not self.enabled:
            return
        
        total_trades = report_data.get('total_trades', 0)
        winning_trades = report_data.get('winning_trades', 0)
        total_pnl = report_data.get('total_pnl', 0)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        pnl_emoji = "📈" if total_pnl > 0 else "📉" if total_pnl < 0 else "➖"
        
        message = (
            f"📊 DAILY REPORT\n"
            f"Date: {datetime.now().strftime('%Y-%m-%d')}\n"
            f"Total Trades: {total_trades}\n"
            f"Winning Trades: {winning_trades}\n"
            f"Win Rate: {win_rate:.1f}%\n"
            f"{pnl_emoji} Total PnL: ${total_pnl:.6f}\n"
        )
        
        if 'balance' in report_data:
            message += f"Current Balance: ${report_data['balance']:.2f}\n"
        
        if 'open_positions' in report_data:
            message += f"Open Positions: {report_data['open_positions']}"
        
        await self.send_message(message)

    def setup_bot_commands(self, bot_instance):
        """Setup bot command handlers"""
        self.bot_instance = bot_instance
        
        application = Application.builder().token(self.bot_token).build()
        
        # Add command handlers
        application.add_handler(CommandHandler("start", self.cmd_start))
        application.add_handler(CommandHandler("status", self.cmd_status))
        application.add_handler(CommandHandler("balance", self.cmd_balance))
        application.add_handler(CommandHandler("trades", self.cmd_trades))
        application.add_handler(CommandHandler("stop", self.cmd_stop))
        application.add_handler(CommandHandler("resume", self.cmd_resume))
        application.add_handler(CommandHandler("help", self.cmd_help))
        application.add_handler(CommandHandler("config", self.cmd_config))
        
        self.application = application
        
        return application

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_message = (
            "🤖 AkibaCryptoBot activated!\n\n"
            "Available commands:\n"
            "/status - Bot status\n"
            "/balance - Account balance\n"
            "/trades - Active trades\n"
            "/stop - Stop trading\n"
            "/resume - Resume trading\n"
            "/help - Show this help\n"
            "/config - Bot configuration"
        )
        await update.message.reply_text(welcome_message)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        if hasattr(self, 'bot_instance'):
            status_info = await self.bot_instance.get_status_info()
            message = (
                f"🤖 Bot Status: {self.bot_status}\n"
                f"Active Trades: {status_info.get('active_trades', 0)}\n"
                f"Daily PnL: ${status_info.get('daily_pnl', 0):.6f}\n"
                f"Uptime: {status_info.get('uptime', 'Unknown')}"
            )
        else:
            message = f"🤖 Bot Status: {self.bot_status}\nNo detailed info available"
        
        await update.message.reply_text(message)

    async def cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /balance command"""
        if hasattr(self, 'bot_instance'):
            try:
                balance_info = await self.bot_instance.get_balance_info()
                message = "💰 Account Balance:\n"
                for currency, amount in balance_info.items():
                    if amount > 0:
                        message += f"{currency}: {amount:.6f}\n"
            except Exception as e:
                message = f"❌ Error fetching balance: {str(e)}"
        else:
            message = "❌ Bot instance not available"
        
        await update.message.reply_text(message)

    async def cmd_trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /trades command"""
        if hasattr(self, 'bot_instance'):
            try:
                trades_info = await self.bot_instance.get_active_trades()
                if not trades_info:
                    message = "📊 No active trades"
                else:
                    message = "📊 Active Trades:\n"
                    for symbol, trade in trades_info.items():
                        side = trade['side'].upper()
                        amount = trade['amount']
                        entry = trade['entry_price']
                        message += f"{symbol}: {side} {amount} @ ${entry:.6f}\n"
            except Exception as e:
                message = f"❌ Error fetching trades: {str(e)}"
        else:
            message = "❌ Bot instance not available"
        
        await update.message.reply_text(message)

    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stop command"""
        self.bot_status = "stopped"
        if hasattr(self, 'bot_instance'):
            await self.bot_instance.pause_trading()
        
        await update.message.reply_text("🛑 Trading stopped")

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /resume command"""
        self.bot_status = "running"
        if hasattr(self, 'bot_instance'):
            await self.bot_instance.resume_trading()
        
        await update.message.reply_text("▶️ Trading resumed")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_message = (
            "🤖 AkibaCryptoBot Commands:\n\n"
            "📊 Information:\n"
            "/status - Bot status and performance\n"
            "/balance - Account balance\n"
            "/trades - List active trades\n\n"
            "🎮 Control:\n"
            "/stop - Pause trading\n"
            "/resume - Resume trading\n\n"
            "⚙️ Configuration:\n"
            "/config - Show bot configuration\n\n"
            "❓ Help:\n"
            "/help - Show this help message\n"
            "/start - Restart bot welcome"
        )
        await update.message.reply_text(help_message)

    async def cmd_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /config command"""
        if hasattr(self, 'bot_instance'):
            try:
                config_info = await self.bot_instance.get_config_info()
                message = (
                    "⚙️ Bot Configuration:\n"
                    f"Trading Mode: {config_info.get('trading_mode', 'Unknown')}\n"
                    f"Max Position Size: {config_info.get('max_position_size', 0)*100:.1f}%\n"
                    f"Stop Loss: {config_info.get('stop_loss_percent', 0)*100:.1f}%\n"
                    f"Take Profit: {config_info.get('take_profit_percent', 0)*100:.1f}%\n"
                    f"Max Open Trades: {config_info.get('max_open_trades', 0)}\n"
                    f"Trading Pairs: {', '.join(config_info.get('pairs', []))}"
                )
            except Exception as e:
                message = f"❌ Error fetching config: {str(e)}"
        else:
            message = "❌ Bot instance not available"
        
        await update.message.reply_text(message)

    async def start_webhook(self, webhook_url: str, port: int = 8443):
        """Start webhook for receiving Telegram updates"""
        if not self.enabled or not self.application:
            return
        
        try:
            await self.application.bot.set_webhook(url=webhook_url)
            await self.application.run_webhook(
                listen="0.0.0.0",
                port=port,
                webhook_url=webhook_url
            )
            logger.info(f"Telegram webhook started on port {port}")
        except Exception as e:
            logger.error(f"Error starting Telegram webhook: {e}")

    async def close(self):
        """Close Telegram connections"""
        if self.application:
            await self.application.stop()
        if hasattr(self, 'bot'):
            # Bot doesn't need explicit closing in python-telegram-bot v20+
            pass