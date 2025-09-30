# QWEN.md - Codebase Context for akibaCryptobot

## Project Overview

AkibaCryptoBot is an autonomous cryptocurrency trading bot built in Python. It's designed for spot trading on Binance with support for both paper trading (sandbox) and live trading. The bot uses technical analysis strategies and includes comprehensive risk management, monitoring, and notification systems.

## Architecture

### Core Components

1. **Main Bot (`src/bot.py`)**
   - Primary orchestrator that manages the trading cycle
   - Coordinates all components and runs the main event loop
   - Implements async/await patterns for non-blocking I/O
   - Handles graceful shutdown via signal handlers

2. **Exchange Layer (`src/exchange/binance_client.py`)**
   - Handles all Binance API interactions using CCXT library
   - Market data fetching (OHLCV, tickers, order book)
   - Order management (place, cancel, status)
   - Account information and balances
   - Supports both sandbox and live trading modes

3. **Strategy Framework (`src/strategies/`)**
   - Modular trading strategy system using Strategy pattern
   - `base_strategy.py` - Abstract base class defining strategy interface
   - `momentum_strategy.py` - Concrete momentum-based strategy implementation
   - Signal generation, position sizing, and performance tracking
   - Support for signal types: BUY, SELL, HOLD, CLOSE_LONG, CLOSE_SHORT

4. **Risk Management (`src/risk_management.py`)**
   - Position sizing calculations
   - Stop-loss and take-profit mechanisms
   - Portfolio risk controls
   - Trailing stop functionality
   - Daily loss limits and position count controls

5. **Technical Indicators (`src/indicators/technical_indicators.py`)**
   - Comprehensive technical analysis tools
   - Multiple indicators including RSI, MACD, Bollinger Bands, Stochastic, ADX, etc.
   - Advanced indicators like Hull MA, SuperTrend, KAMA, MFI
   - Pattern recognition utilities

6. **Notifications (`src/notifications/telegram_bot.py`)**
   - Telegram integration for trade alerts and bot status updates
   - Command handling (/status, /balance, /trades, /stop, /resume, etc.)
   - Error notifications and daily reports

7. **Logging System (`src/bot_logging/logger.py`)**
   - Structured logging with configurable levels
   - Separate log files for trades, errors, and performance
   - Log rotation and formatting

### Configuration Structure

The main configuration (`config/config.json`) includes:
- Exchange settings (sandbox mode, rate limits)
- Trading pairs and timeframes
- Strategy selection and parameters
- Risk management rules (position sizes, stop-loss/take-profit percentages)
- Notification preferences
- Logging configuration
- Backtesting parameters

## Development Environment

### Prerequisites
- Python 3.9+
- Binance Account with API access
- Telegram Bot (optional, for notifications)

### Installation Steps

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Environment Configuration**:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` with your credentials:
   - `BINANCE_API_KEY` and `BINANCE_SECRET_KEY`
   - `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (optional)

3. **Configure Trading Parameters**:
   Edit `config/config.json` to adjust trading pairs, risk parameters, etc.

## Running the Bot

### Start the Bot
```bash
python src/bot.py
```

The bot will start in paper trading mode (sandbox) by default.

### Check Logs
Monitor the bot's activity:
```bash
# Main logs
tail -f logs/akiba_crypto_bot.log

# Trade logs
tail -f logs/trades.log

# Error logs
tail -f logs/errors.log
```

### Telegram Commands (if configured)
- `/start` - Initialize bot
- `/status` - Check bot status
- `/balance` - View account balance
- `/trades` - See active trades
- `/stop` - Pause trading
- `/resume` - Resume trading

## Code Quality and Testing

### Code Quality Tools
```bash
# Format code with black
black src/ tests/

# Lint with flake8
flake8 src/ tests/

# Type checking with mypy
mypy src/
```

### Testing Framework
- Uses pytest for testing
- Currently has an empty tests directory (needs implementation)
- Focus on strategy signal generation, exchange client mock testing, risk management calculations

## Key Design Patterns

- **Async/Await**: All exchange operations and main loops use async patterns for non-blocking I/O
- **Strategy Pattern**: Trading strategies inherit from BaseStrategy and can be swapped via StrategyManager
- **Signal-Based Trading**: Strategies generate TradingSignal objects with metadata and strength indicators
- **Configuration-Driven**: All settings managed through config/config.json and environment variables

## Security Considerations

- All sensitive credentials must be in `.env` file, never hardcoded
- Bot defaults to paper trading mode (sandbox=true)
- API keys should have minimal required permissions
- Use IP restrictions on Binance API keys
- Never commit `.env` files or credentials to git

## Trading Safety

- Implements comprehensive risk management with position sizing limits
- Includes stop-loss and take-profit mechanisms
- Supports emergency stop functionality via signal handlers
- Trailing stop functionality
- Daily loss limits and position count controls

## Strategy Development

New strategies should:
1. Inherit from BaseStrategy
2. Implement `generate_signal()` and `calculate_indicators()` methods
3. Signal strength should be 0.0 to 1.0 (affects position sizing)
4. Use `validate_signal()` to ensure minimum quality thresholds

## Error Handling

- All exchange operations wrapped in try/catch with logging
- Graceful shutdown on SIGINT/SIGTERM signals
- Telegram notifications for critical errors
- Bot continues running despite individual operation failures

## Performance Monitoring

- Strategies track win rate, total PnL, and signal counts
- Performance metrics available via `get_performance_summary()`
- Separate logging for trades, errors, and performance metrics
- Supports performance alerts via Telegram

## Dependencies

Key dependencies include:
- `ccxt` - Cryptocurrency trading library
- `python-binance` - Official Binance API connector
- `pandas`, `numpy` - Data analysis and manipulation
- `ta` - Technical analysis library
- `python-telegram-bot` - Telegram integration
- `python-dotenv` - Environment variable management
- `pytest` - Testing framework
- `black`, `flake8`, `mypy` - Code quality tools

## Project Status

The project has a complete core architecture with:
- Main trading bot implementation
- Exchange integration with Binance
- Momentum-based trading strategy
- Risk management system
- Telegram notifications
- Comprehensive logging
- Configuration system

The main missing components are:
- Backtesting engine (empty directory)
- Test suite (empty tests directory)
- CI/CD configuration
- Docker deployment files