import logging
import logging.handlers
import os
from datetime import datetime
from typing import Dict, Optional


def setup_logging(config: Dict) -> logging.Logger:
    """Setup logging configuration for the bot"""

    # Create logs directory if it doesn't exist
    log_dir = os.path.dirname(config.get("file", "logs/akiba_crypto_bot.log"))
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Configure root logger
    logger = logging.getLogger("akiba_crypto_bot")
    logger.setLevel(getattr(logging, config.get("level", "INFO")))

    # Clear existing handlers
    logger.handlers.clear()

    # Create formatter
    formatter = logging.Formatter(
        config.get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler with rotation
    if config.get("file"):
        file_handler = logging.handlers.RotatingFileHandler(
            config["file"],
            maxBytes=config.get("max_bytes", 10485760),  # 10MB
            backupCount=config.get("backup_count", 5),
        )
        file_handler.setLevel(getattr(logging, config.get("level", "INFO")))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Trade logger - separate file for trade-specific logs
    trade_logger = logging.getLogger("akiba_crypto_bot.trades")
    trade_handler = logging.handlers.RotatingFileHandler(
        "logs/trades.log", maxBytes=5242880, backupCount=10  # 5MB
    )
    trade_formatter = logging.Formatter(
        "%(asctime)s - TRADE - %(levelname)s - %(message)s"
    )
    trade_handler.setFormatter(trade_formatter)
    trade_logger.addHandler(trade_handler)
    trade_logger.setLevel(logging.INFO)

    # Error logger - separate file for errors
    error_logger = logging.getLogger("akiba_crypto_bot.errors")
    error_handler = logging.handlers.RotatingFileHandler(
        "logs/errors.log", maxBytes=5242880, backupCount=10  # 5MB
    )
    error_formatter = logging.Formatter(
        "%(asctime)s - ERROR - %(name)s - %(levelname)s - %(message)s - %(pathname)s:%(lineno)d"
    )
    error_handler.setFormatter(error_formatter)
    error_logger.addHandler(error_handler)
    error_logger.setLevel(logging.ERROR)

    logger.info("Logging system initialized")
    return logger


class TradeLogger:
    """Specialized logger for trade events"""

    def __init__(self):
        self.logger = logging.getLogger("akiba_crypto_bot.trades")

    def log_trade_entry(
        self, symbol: str, side: str, amount: float, price: float, order_id: str
    ):
        """Log trade entry"""
        self.logger.info(
            f"ENTRY - {symbol} - {side.upper()} - Amount: {amount} - Price: {price} - Order: {order_id}"
        )

    def log_trade_exit(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        reason: str,
        pnl: float,
        order_id: str,
    ):
        """Log trade exit"""
        pnl_str = f"+{pnl:.4f}" if pnl > 0 else f"{pnl:.4f}"
        self.logger.info(
            f"EXIT - {symbol} - {side.upper()} - Amount: {amount} - Price: {price} - "
            f"Reason: {reason} - PnL: {pnl_str} - Order: {order_id}"
        )

    def log_order_update(self, symbol: str, order_id: str, status: str, filled: float):
        """Log order status updates"""
        self.logger.info(
            f"ORDER_UPDATE - {symbol} - Order: {order_id} - Status: {status} - Filled: {filled}"
        )


class ErrorLogger:
    """Specialized logger for error tracking"""

    def __init__(self):
        self.logger = logging.getLogger("akiba_crypto_bot.errors")

    def log_exchange_error(self, error: Exception, context: str):
        """Log exchange-related errors"""
        self.logger.error(f"EXCHANGE_ERROR - {context} - {str(error)}", exc_info=True)

    def log_strategy_error(self, error: Exception, strategy: str, symbol: str):
        """Log strategy-related errors"""
        self.logger.error(
            f"STRATEGY_ERROR - Strategy: {strategy} - Symbol: {symbol} - {str(error)}",
            exc_info=True,
        )

    def log_risk_management_error(self, error: Exception, context: str):
        """Log risk management errors"""
        self.logger.error(f"RISK_ERROR - {context} - {str(error)}", exc_info=True)


class PerformanceLogger:
    """Logger for performance metrics"""

    def __init__(self):
        self.logger = logging.getLogger("akiba_crypto_bot.performance")

        # Setup performance log file
        perf_handler = logging.handlers.RotatingFileHandler(
            "logs/performance.log", maxBytes=10485760, backupCount=5  # 10MB
        )
        perf_formatter = logging.Formatter("%(asctime)s - PERF - %(message)s")
        perf_handler.setFormatter(perf_formatter)
        self.logger.addHandler(perf_handler)
        self.logger.setLevel(logging.INFO)

    def log_daily_summary(
        self,
        total_trades: int,
        winning_trades: int,
        total_pnl: float,
        max_drawdown: float,
    ):
        """Log daily performance summary"""
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        self.logger.info(
            f"DAILY_SUMMARY - Trades: {total_trades} - Win Rate: {win_rate:.2f}% - "
            f"PnL: {total_pnl:.4f} - Max DD: {max_drawdown:.4f}"
        )

    def log_strategy_performance(
        self, strategy: str, symbol: str, trades: int, win_rate: float, pnl: float
    ):
        """Log strategy-specific performance"""
        self.logger.info(
            f"STRATEGY_PERF - {strategy} - {symbol} - Trades: {trades} - "
            f"Win Rate: {win_rate:.2f}% - PnL: {pnl:.4f}"
        )


# Global logger instances
trade_logger = TradeLogger()
error_logger = ErrorLogger()
performance_logger = PerformanceLogger()
