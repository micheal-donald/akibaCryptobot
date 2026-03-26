import os
from typing import Any, Dict
import logging
from datetime import datetime
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from runtime.view_models import ProjectionSource
from runtime.rollout import build_rollout_summary

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

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
                chat_id=self.chat_id, text=message, parse_mode=parse_mode
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

        action = trade_data.get("action", "UNKNOWN")
        symbol = trade_data.get("symbol", "UNKNOWN")
        side = trade_data.get("side", "").upper()
        amount = trade_data.get("amount", 0)
        price = trade_data.get("price", 0)

        if action == "entry":
            message = (
                f"📈 NEW {side} TRADE\n"
                f"Symbol: {symbol}\n"
                f"Amount: {amount}\n"
                f"Entry Price: ${price:.6f}\n"
                f"Time: {datetime.now().strftime('%H:%M:%S')}"
            )

            if "stop_loss" in trade_data:
                message += f"\nStop Loss: ${trade_data['stop_loss']:.6f}"
            if "take_profit" in trade_data:
                message += f"\nTake Profit: ${trade_data['take_profit']:.6f}"

        elif action == "exit":
            pnl = trade_data.get("pnl", 0)
            reason = trade_data.get("reason", "manual")
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

        total_trades = report_data.get("total_trades", 0)
        winning_trades = report_data.get("winning_trades", 0)
        total_pnl = report_data.get("total_pnl", 0)
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

        if "balance" in report_data:
            message += f"Current Balance: ${report_data['balance']:.2f}\n"

        if "open_positions" in report_data:
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
        application.add_handler(CommandHandler("performance", self.cmd_performance))
        application.add_handler(CommandHandler("metrics", self.cmd_metrics))
        application.add_handler(CommandHandler("report", self.cmd_report))
        application.add_handler(CommandHandler("risk", self.cmd_risk))
        application.add_handler(CommandHandler("correlations", self.cmd_correlations))
        application.add_handler(CommandHandler("heatmap", self.cmd_heatmap))
        application.add_handler(CommandHandler("reconcile", self.cmd_reconcile))

        self.application = application

        return application

    def _get_runtime_projection(self):
        """Return the current runtime projection for operator-facing commands."""
        if not hasattr(self, "bot_instance"):
            return None

        projection_getter = getattr(self.bot_instance, "get_runtime_projection", None)
        if projection_getter is None:
            return None

        try:
            return projection_getter()
        except Exception as exc:
            logger.error("Failed to load runtime projection for Telegram: %s", exc)
            return None

    @staticmethod
    def _projection_source_value(projection: Any) -> str:
        if projection is None:
            return ProjectionSource.LEGACY.value
        source = getattr(projection, "source", None)
        return getattr(source, "value", str(source or ProjectionSource.LEGACY.value))

    @classmethod
    def _uses_runtime_projection(cls, projection: Any) -> bool:
        return (
            cls._projection_source_value(projection)
            == ProjectionSource.RUNTIME_PROJECTION.value
        )

    @classmethod
    def _runtime_summary_lines(cls, projection: Any) -> list[str]:
        if projection is None:
            return []

        return [
            f"Runtime Mode: {getattr(projection, 'runtime_mode', 'legacy')}",
            f"State Source: {cls._projection_source_value(projection)}",
            f"Runtime Running: {'yes' if getattr(projection, 'runtime_is_running', False) else 'no'}",
            f"Runtime Healthy: {'yes' if getattr(projection, 'runtime_is_healthy', True) else 'no'}",
            f"Runtime Start Status: {getattr(projection, 'runtime_start_status', 'disabled')}",
            f"Runtime Start Pending: {getattr(projection, 'runtime_start_pending', False)}",
            f"Runtime Owns Entries: {getattr(projection, 'runtime_owns_entries', False)}",
            f"Runtime Owns Exits: {getattr(projection, 'runtime_owns_exits', False)}",
        ]

    @classmethod
    def _rollout_summary_lines(cls, projection: Any) -> list[str]:
        if projection is None:
            return []
        rollout = build_rollout_summary(projection)

        return [
            f"Rollout Health: {rollout['rollout_health']}",
            f"Rollout Recommended Action: {rollout['rollout_recommended_action']}",
            f"Rollout Summary: {rollout['rollout_summary']}",
        ]

    @staticmethod
    def _format_trade_lines(trades_info: Any) -> list[str]:
        if isinstance(trades_info, dict):
            entries = trades_info.items()
        else:
            entries = ((trade.get("symbol", "UNKNOWN"), trade) for trade in trades_info)

        lines = []
        for symbol, trade in entries:
            side = str(trade.get("side", "")).upper()
            amount = trade.get("amount", 0)
            entry = trade.get("entry_price")
            status = trade.get("status")
            entry_text = f"${entry:.6f}" if entry is not None else "n/a"
            status_suffix = f" [{status}]" if status else ""
            lines.append(f"{symbol}: {side} {amount} @ {entry_text}{status_suffix}")
        return lines

    @staticmethod
    def _format_projected_trade_lines(projection: Any) -> list[str]:
        lines = []
        for position in getattr(projection, "positions", ()):
            side = str(getattr(position, "side", "")).upper()
            amount = getattr(position, "open_qty", 0)
            entry = getattr(position, "avg_entry_price", None)
            entry_text = f"${entry:.6f}" if entry is not None else "n/a"
            status = getattr(position, "status", "unknown")
            lines.append(
                f"{position.symbol}: {side} {amount} @ {entry_text} [{status}]"
            )
        return lines

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_message = (
            "🤖 AkibaCryptoBot activated!\n\n"
            "Available commands:\n"
            "/status - Bot status\n"
            "/balance - Account balance\n"
            "/trades - Active trades\n"
            "/performance - Performance summary\n"
            "/metrics - Detailed metrics\n"
            "/report - Full performance report\n"
            "/risk - Risk management report\n"
            "/correlations - Asset correlations\n"
            "/heatmap - Portfolio risk heatmap\n"
            "/stop - Stop trading\n"
            "/resume - Resume trading\n"
            "/reconcile - Manual balance audit\n"
            "/help - Show this help\n"
            "/config - Bot configuration"
        )
        await update.message.reply_text(welcome_message)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        if hasattr(self, "bot_instance"):
            try:
                status_info = self.bot_instance.get_status_info()
                projection = self._get_runtime_projection()
                active_trades = status_info.get("active_trades", 0)
                if self._uses_runtime_projection(projection):
                    active_trades = getattr(projection, "active_position_count", 0)

                lines = [
                    f"Bot Status: {self.bot_status}",
                    f"Active Trades: {active_trades}",
                    f"Daily PnL: ${status_info.get('daily_pnl', 0):.6f}",
                    f"Uptime: {status_info.get('uptime', 'Unknown')}",
                ]

                if projection is not None:
                    lines.extend(self._runtime_summary_lines(projection))
                    lines.extend(self._rollout_summary_lines(projection))
                    lines.append(
                        f"Projected Open Orders: {getattr(projection, 'open_order_count', 0)}"
                    )
                    lines.append(
                        f"Projected Pending Exits: {getattr(projection, 'pending_exit_count', 0)}"
                    )

                message = "\n".join(lines)
            except Exception as e:
                message = f"Error fetching status: {str(e)}"
        else:
            message = f"Bot Status: {self.bot_status}\nNo detailed info available"

        await update.message.reply_text(message)

    async def cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /balance command"""
        if hasattr(self, "bot_instance"):
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
        if hasattr(self, "bot_instance"):
            try:
                projection = self._get_runtime_projection()
                if self._uses_runtime_projection(projection):
                    trade_lines = self._format_projected_trade_lines(projection)
                    if not trade_lines:
                        lines = ["No active trades"]
                    else:
                        lines = ["Active Trades:", *trade_lines]
                    lines.extend(self._runtime_summary_lines(projection))
                    lines.extend(self._rollout_summary_lines(projection))
                    lines.append(
                        f"Projected Open Orders: {getattr(projection, 'open_order_count', 0)}"
                    )
                    message = "\n".join(lines)
                else:
                    trades_info = self.bot_instance.get_active_trades()
                    if not trades_info:
                        lines = ["No active trades"]
                    else:
                        lines = [
                            "Active Trades:",
                            *self._format_trade_lines(trades_info),
                        ]

                    if (
                        self._projection_source_value(projection)
                        == ProjectionSource.SHADOW.value
                    ):
                        lines.append(
                            f"Shadow Projected Positions: {getattr(projection, 'active_position_count', 0)}"
                        )
                        lines.append(
                            f"Shadow Open Orders: {getattr(projection, 'open_order_count', 0)}"
                        )
                        lines.extend(self._runtime_summary_lines(projection))
                        lines.extend(self._rollout_summary_lines(projection))

                    message = "\n".join(lines)
            except Exception as e:
                message = f"Error fetching trades: {str(e)}"
        else:
            message = "Bot instance not available"

        await update.message.reply_text(message)

    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stop command"""
        self.bot_status = "stopped"
        if hasattr(self, "bot_instance"):
            self.bot_instance.pause_trading()

        await update.message.reply_text("🛑 Trading stopped")

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /resume command"""
        self.bot_status = "running"
        if hasattr(self, "bot_instance"):
            self.bot_instance.resume_trading()

        await update.message.reply_text("▶️ Trading resumed")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_message = (
            "🤖 AkibaCryptoBot Commands:\n\n"
            "📊 Information:\n"
            "/status - Bot status and performance\n"
            "/balance - Account balance\n"
            "/trades - List active trades\n"
            "/performance - Quick performance summary\n"
            "/metrics - Detailed performance metrics\n"
            "/report - Full formatted report\n\n"
            "🎮 Control:\n"
            "/stop - Pause trading\n"
            "/resume - Resume trading\n\n"
            "⚙️ Configuration:\n"
            "/config - Show bot configuration\n\n"
            "🛠 Tools:\n"
            "/reconcile - Audit DB vs Exchange\n\n"
            "❓ Help:\n"
            "/help - Show this help message\n"
            "/start - Restart bot welcome"
        )
        await update.message.reply_text(help_message)

    async def cmd_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /config command"""
        if hasattr(self, "bot_instance"):
            try:
                config_info = self.bot_instance.get_config_info()
                message = (
                    "⚙️ Bot Configuration:\n"
                    f"Trading Mode: {config_info.get('trading_mode', 'Unknown')}\n"
                    f"Max Position Size: {config_info.get('max_position_size', 0) * 100:.1f}%\n"
                    f"Stop Loss: {config_info.get('stop_loss_percent', 0) * 100:.1f}%\n"
                    f"Take Profit: {config_info.get('take_profit_percent', 0) * 100:.1f}%\n"
                    f"Max Open Trades: {config_info.get('max_open_trades', 0)}\n"
                    f"Trading Pairs: {', '.join(config_info.get('pairs', []))}"
                )
            except Exception as e:
                message = f"❌ Error fetching config: {str(e)}"
        else:
            message = "❌ Bot instance not available"

        await update.message.reply_text(message)

    async def cmd_reconcile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /reconcile command - manually trigger audit"""
        if hasattr(self, "bot_instance") and hasattr(
            self.bot_instance, "reconciliation"
        ):
            await update.message.reply_text(
                "🔍 Starting manual reconciliation audit..."
            )
            try:
                discrepancies = await self.bot_instance.reconciliation.reconcile()
                orphans = len(discrepancies.get("orphans", []))
                lost = len(discrepancies.get("lost_trades", []))
                mismatches = len(discrepancies.get("mismatches", []))

                if orphans == 0 and lost == 0 and mismatches == 0:
                    await update.message.reply_text(
                        "✅ Reconciliation complete: Local DB matches Exchange state."
                    )
                else:
                    msg = (
                        "⚖️ Reconciliation Results:\n"
                        f"• Orphans: {orphans}\n"
                        f"• Lost Trades: {lost}\n"
                        f"• Mismatches: {mismatches}\n\n"
                        "Check bot logs for specific details."
                    )
                    await update.message.reply_text(msg)
            except Exception as e:
                await update.message.reply_text(f"❌ Reconciliation failed: {str(e)}")
        else:
            await update.message.reply_text("❌ Reconciliation manager not available")

    async def start_webhook(self, webhook_url: str, port: int = 8443):
        """Start webhook for receiving Telegram updates"""
        if not self.enabled or not self.application:
            return

        try:
            await self.application.bot.set_webhook(url=webhook_url)
            await self.application.run_webhook(
                listen="0.0.0.0", port=port, webhook_url=webhook_url
            )
            logger.info(f"Telegram webhook started on port {port}")
        except Exception as e:
            logger.error(f"Error starting Telegram webhook: {e}")

    async def cmd_performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /performance command - quick summary"""
        if hasattr(self, "bot_instance") and hasattr(
            self.bot_instance, "performance_tracker"
        ):
            try:
                metrics = (
                    await self.bot_instance.performance_tracker.get_metrics_summary(
                        trade_store=self.bot_instance.trade_store
                    )
                )
                projection = self._get_runtime_projection()

                pnl_state = (
                    "profit"
                    if metrics["total_pnl"] > 0
                    else "loss" if metrics["total_pnl"] < 0 else "flat"
                )
                open_positions = metrics.get("open_trades_count", 0)
                if self._uses_runtime_projection(projection):
                    open_positions = getattr(projection, "active_position_count", 0)

                lines = [
                    "Performance Summary",
                    "",
                    f"Balance: ${metrics['current_balance']:,.2f}",
                    f"Total P&L ({pnl_state}): ${metrics['total_pnl']:,.2f} ({metrics['total_return_percent']:.2f}%)",
                    "",
                    f"Trades: {metrics['total_trades']}",
                    f"Win Rate: {metrics['win_rate']:.1f}%",
                    f"Profit Factor: {metrics['profit_factor']:.2f}",
                    f"Max DD: {metrics['max_drawdown_percent']:.2f}%",
                    f"Open Positions: {open_positions}",
                ]

                if projection is not None:
                    if (
                        self._projection_source_value(projection)
                        == ProjectionSource.SHADOW.value
                    ):
                        lines.append(
                            f"Shadow Projected Positions: {getattr(projection, 'active_position_count', 0)}"
                        )
                    lines.extend(self._runtime_summary_lines(projection))
                    lines.extend(self._rollout_summary_lines(projection))

                message = "\n".join(lines)
            except Exception as e:
                message = f"Error fetching performance: {str(e)}"
        else:
            message = "Performance tracker not available"

        await update.message.reply_text(message)

    async def cmd_metrics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /metrics command - detailed metrics"""
        if hasattr(self, "bot_instance") and hasattr(
            self.bot_instance, "performance_tracker"
        ):
            try:
                metrics = (
                    await self.bot_instance.performance_tracker.get_metrics_summary(
                        trade_store=self.bot_instance.trade_store
                    )
                )
                projection = self._get_runtime_projection()
                open_positions = metrics.get("open_trades_count", 0)
                if self._uses_runtime_projection(projection):
                    open_positions = getattr(projection, "active_position_count", 0)

                lines = [
                    "Detailed Performance Metrics",
                    "",
                    "BALANCE",
                    f"Initial: ${metrics['initial_balance']:,.2f}",
                    f"Current: ${metrics['current_balance']:,.2f}",
                    f"P&L: ${metrics['total_pnl']:,.2f} ({metrics['total_return_percent']:.2f}%)",
                    "",
                    "TRADES",
                    f"Total: {metrics['total_trades']}",
                    f"Winners: {metrics['winning_trades']}",
                    f"Losers: {metrics['losing_trades']}",
                    f"Win Rate: {metrics['win_rate']:.2f}%",
                    f"Avg Win: ${metrics['average_win']:.2f}",
                    f"Avg Loss: ${metrics['average_loss']:.2f}",
                    f"W/L Ratio: {metrics['win_loss_ratio']:.2f}",
                    "",
                    "RISK METRICS",
                    f"Sharpe Ratio: {metrics['sharpe_ratio']:.3f}",
                    f"Sortino Ratio: {metrics['sortino_ratio']:.3f}",
                    f"Profit Factor: {metrics['profit_factor']:.3f}",
                    f"Expectancy: ${metrics['expectancy']:.2f}",
                    "",
                    "DRAWDOWN",
                    f"Max DD: {metrics['max_drawdown_percent']:.2f}%",
                    f"Current DD: {metrics['current_drawdown_percent']:.2f}%",
                    "",
                    f"Open Positions: {open_positions}",
                ]

                if projection is not None:
                    if (
                        self._projection_source_value(projection)
                        == ProjectionSource.SHADOW.value
                    ):
                        lines.append(
                            f"Shadow Projected Positions: {getattr(projection, 'active_position_count', 0)}"
                        )
                    lines.extend(self._runtime_summary_lines(projection))
                    lines.extend(self._rollout_summary_lines(projection))

                message = "\n".join(lines)
            except Exception as e:
                message = f"Error fetching metrics: {str(e)}"
        else:
            message = "Performance tracker not available"

        await update.message.reply_text(message)

    async def cmd_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /report command - full formatted report"""
        if hasattr(self, "bot_instance") and hasattr(
            self.bot_instance, "performance_tracker"
        ):
            try:
                report = await self.bot_instance.performance_tracker.generate_performance_report(
                    trade_store=self.bot_instance.trade_store
                )

                # Telegram has 4096 character limit, so we might need to split
                if len(report) > 4000:
                    # Send in chunks
                    chunks = [report[i : i + 4000] for i in range(0, len(report), 4000)]
                    for chunk in chunks:
                        await update.message.reply_text(
                            f"```\n{chunk}\n```", parse_mode="Markdown"
                        )
                else:
                    await update.message.reply_text(
                        f"```\n{report}\n```", parse_mode="Markdown"
                    )
            except Exception as e:
                message = f"❌ Error generating report: {str(e)}"
                await update.message.reply_text(message)
        else:
            await update.message.reply_text("❌ Performance tracker not available")

    async def cmd_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /risk command - show comprehensive risk report"""
        if hasattr(self, "bot_instance") and hasattr(
            self.bot_instance, "advanced_risk"
        ):
            try:
                risk_report = self.bot_instance.advanced_risk.get_risk_report()

                # Circuit breaker status
                cb_status = (
                    "🟢 ACTIVE"
                    if self.bot_instance.circuit_breaker_active
                    else "⚪ Inactive"
                )
                cb_reason = (
                    f"\nReason: {self.bot_instance.circuit_breaker_reason}"
                    if self.bot_instance.circuit_breaker_active
                    else ""
                )

                message = (
                    f"🛡️ Risk Management Report\n\n"
                    f"📊 Circuit Breaker: {cb_status}{cb_reason}\n\n"
                    f"⚙️ Position Sizing:\n"
                    f"  • ATR Period: {risk_report['position_sizing_parameters']['atr_period']}\n"
                    f"  • ATR Multiplier: {risk_report['position_sizing_parameters']['atr_multiplier']}\n"
                    f"  • Kelly Enabled: {risk_report['position_sizing_parameters']['kelly_enabled']}\n"
                    f"  • Kelly Cap: {risk_report['position_sizing_parameters']['kelly_cap']:.2f}\n\n"
                    f"🔗 Correlation Limits:\n"
                    f"  • Threshold: {risk_report['correlation_parameters']['threshold']:.2f}\n"
                    f"  • Max Correlated: {risk_report['correlation_parameters']['max_correlated_positions']}\n\n"
                    f"🚨 Circuit Breakers:\n"
                    f"  • Volatility Threshold: {risk_report['circuit_breaker_parameters']['volatility_threshold']:.1%}\n"
                    f"  • Drawdown Threshold: {risk_report['circuit_breaker_parameters']['drawdown_threshold']:.1%}\n\n"
                    f"📈 Portfolio Risk: {risk_report['portfolio_heatmap']['total_risk']:.2f}%"
                )

                await update.message.reply_text(message)
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Error generating risk report: {str(e)}"
                )
        else:
            await update.message.reply_text("❌ Advanced risk manager not available")

    async def cmd_correlations(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle /correlations command - show asset correlations"""
        if hasattr(self, "bot_instance") and hasattr(
            self.bot_instance, "advanced_risk"
        ):
            try:
                correlations = self.bot_instance.advanced_risk.asset_correlations

                if not correlations:
                    await update.message.reply_text(
                        "📊 No correlation data available yet.\nNeed at least 2 trading pairs with sufficient history."
                    )
                    return

                message = "🔗 Asset Correlations\n\n"

                # Sort by absolute correlation value
                sorted_corr = sorted(
                    correlations.items(), key=lambda x: abs(x[1]), reverse=True
                )

                for (symbol1, symbol2), corr in sorted_corr[:15]:  # Show top 15
                    # Emoji based on correlation strength
                    if abs(corr) > 0.7:
                        emoji = "🔴"  # High correlation
                    elif abs(corr) > 0.4:
                        emoji = "🟡"  # Moderate correlation
                    else:
                        emoji = "🟢"  # Low correlation

                    message += f"{emoji} {symbol1} ↔ {symbol2}: {corr:+.3f}\n"

                if len(sorted_corr) > 15:
                    message += f"\n... and {len(sorted_corr) - 15} more pairs"

                await update.message.reply_text(message)
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Error getting correlations: {str(e)}"
                )
        else:
            await update.message.reply_text("❌ Advanced risk manager not available")

    async def cmd_heatmap(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /heatmap command - show portfolio risk heatmap"""
        if hasattr(self, "bot_instance") and hasattr(
            self.bot_instance, "advanced_risk"
        ):
            try:
                # Generate heatmap
                balance = await self.bot_instance.exchange.get_balance()
                usdt_balance = balance.get("USDT", {}).get("free", 0) + balance.get(
                    "USDT", {}
                ).get("used", 0)

                heatmap = self.bot_instance.advanced_risk.generate_portfolio_heatmap(
                    positions=self.bot_instance.active_trades,
                    price_data=self.bot_instance.price_data_cache,
                    balance=usdt_balance,
                )

                if not heatmap.asset_exposures:
                    await update.message.reply_text(
                        "📊 No active positions - portfolio heatmap is empty"
                    )
                    return

                message = "🔥 Portfolio Risk Heatmap\n\n"
                message += (
                    f"⚡ Total Portfolio Risk: {heatmap.total_portfolio_risk:.2f}%\n\n"
                )
                message += "📊 Asset Exposures:\n"

                # Sort by exposure
                sorted_exposures = sorted(
                    heatmap.asset_exposures.items(), key=lambda x: x[1], reverse=True
                )

                for symbol, exposure in sorted_exposures:
                    # Visual bar
                    bar_length = int(exposure / 5)  # 1 char per 5%
                    bar = "█" * min(bar_length, 20)

                    message += f"  {symbol}: {exposure:.1f}% {bar}\n"

                # Check if any positions are highly correlated
                high_corr_warning = ""
                for (s1, s2), corr in heatmap.correlations.items():
                    if (
                        abs(corr) > 0.7
                        and s1 in heatmap.asset_exposures
                        and s2 in heatmap.asset_exposures
                    ):
                        high_corr_warning += f"⚠️ {s1} & {s2}: {corr:+.2f}\n"

                if high_corr_warning:
                    message += f"\n⚠️ High Correlations:\n{high_corr_warning}"

                await update.message.reply_text(message)
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Error generating heatmap: {str(e)}"
                )
        else:
            await update.message.reply_text("❌ Advanced risk manager not available")

    async def close(self):
        """Close Telegram connections"""
        if self.application:
            await self.application.stop()
        if hasattr(self, "bot"):
            # Bot doesn't need explicit closing in python-telegram-bot v20+
            pass
