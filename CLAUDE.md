# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AkibaCryptoBot is an autonomous cryptocurrency trading bot built in Python. It's designed for spot trading on Binance with support for both paper trading (sandbox) and live trading. The bot uses technical analysis strategies and includes comprehensive risk management, monitoring, and notification systems.

## Development Commands

### Running the Bot
```bash
# Start the bot (uses config/config.json)
python src/bot.py

# Run with custom config
python src/bot.py --config path/to/config.json
```

### Testing
```bash
# Install test dependencies
pip install pytest pytest-cov pytest-asyncio

# Run all tests
pytest

# Run tests with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_strategies.py

# Run tests with verbose output
pytest -v
```

### Code Quality
```bash
# Format code with black
black src/ tests/

# Lint with flake8
flake8 src/ tests/

# Type checking with mypy
mypy src/
```

### Environment Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Edit .env with your API keys and configuration
```

## Architecture

### Core Components

1. **Main Bot (`src/bot.py`)** - Primary orchestrator that manages the trading cycle, coordinates all components, and runs the main event loop.

2. **Exchange Layer (`src/exchange/binance_client.py`)** - Handles all Binance API interactions including:
   - Market data fetching (OHLCV, tickers, order book)
   - Order management (place, cancel, status)
   - Account information and balances
   - Supports both sandbox and live trading modes

3. **Strategy Framework (`src/strategies/`)** - Modular trading strategy system:
   - `base_strategy.py` - Abstract base class defining strategy interface
   - Includes signal generation, position sizing, and performance tracking
   - Supports signal types: BUY, SELL, HOLD, CLOSE_LONG, CLOSE_SHORT
   - Built-in validation and exit logic

4. **Risk Management (`src/risk_management.py`)** - Position sizing, stop-loss, take-profit calculations, and portfolio risk controls.

5. **Technical Indicators (`src/indicators/technical_indicators.py`)** - Technical analysis tools for strategy development.

6. **Notifications (`src/notifications/telegram_bot.py`)** - Telegram integration for trade alerts and bot status updates.

7. **Logging System (`src/logging/logger.py`)** - Structured logging with configurable levels and file rotation.

### Key Design Patterns

- **Async/Await**: All exchange operations and main loops use async patterns for non-blocking I/O
- **Strategy Pattern**: Trading strategies inherit from BaseStrategy and can be swapped via StrategyManager
- **Signal-Based Trading**: Strategies generate TradingSignal objects with metadata and strength indicators
- **Configuration-Driven**: All settings managed through config/config.json and environment variables

### Configuration Structure

The main configuration (`config/config.json`) includes:
- Exchange settings (sandbox mode, rate limits)
- Trading pairs and timeframes  
- Strategy selection and parameters
- Risk management rules (position sizes, stop-loss/take-profit percentages)
- Notification preferences
- Logging configuration
- Backtesting parameters

## Important Implementation Notes

### Environment Variables
- All sensitive credentials must be in `.env` file, never hardcoded
- Required: `BINANCE_API_KEY`, `BINANCE_SECRET_KEY`
- Optional: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- Trading mode controlled by `TRADING_MODE` (paper/live)

### Security Considerations
- Always start with sandbox=true for testing
- API keys should have minimal required permissions
- Use IP restrictions on Binance API keys
- Never commit `.env` files or credentials to git

### Trading Safety
- Bot defaults to paper trading mode (sandbox=true)
- Implements comprehensive risk management with position sizing limits
- Includes stop-loss and take-profit mechanisms
- Supports emergency stop functionality via signal handlers

### Strategy Development
- New strategies should inherit from BaseStrategy
- Must implement `generate_signal()` and `calculate_indicators()` methods
- Signal strength should be 0.0 to 1.0 (affects position sizing)
- Use `validate_signal()` to ensure minimum quality thresholds

### Error Handling
- All exchange operations wrapped in try/catch with logging
- Graceful shutdown on SIGINT/SIGTERM signals
- Telegram notifications for critical errors
- Bot continues running despite individual operation failures

### Performance Monitoring
- Strategies track win rate, total PnL, and signal counts
- Performance metrics available via `get_performance_summary()`
- Logging includes trade execution details and errors
- Supports performance alerts via Telegram

## Testing Notes

- Currently, the `tests/` directory exists but appears empty
- When adding tests, focus on:
  - Strategy signal generation and validation
  - Exchange client mock testing (avoid live API calls in tests)
  - Risk management calculations
  - Configuration loading and validation
- Use pytest fixtures for common test data (OHLCV data, mock exchanges)
- All async functions require pytest-asyncio for proper testing