# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AkibaCryptoBot is an autonomous cryptocurrency trading bot built in Python. It's designed for spot trading on Binance with support for both paper trading (sandbox) and live trading. The bot uses technical analysis strategies, FreqAI machine learning integration, and includes comprehensive risk management, monitoring, and notification systems.

---

## Documentation Protocol

> **These rules are mandatory and must be followed in every session.**

### 1 — Read Docs on Initialisation

Before making **any** code changes, read the documentation index first:

```
docs/README.md          ← always read this first
```

Then read the relevant doc for the area you are about to work on:

| Working on… | Read… |
|---|---|
| Trading logic, components, data flow | `docs/ARCHITECTURE.md` |
| `config/config.json` or `.env` | `docs/CONFIGURATION.md` |
| Strategies or signal logic | `docs/STRATEGIES.md` |
| API routes or WebSocket | `docs/API_REFERENCE.md` |
| Telegram commands/notifications | `docs/TELEGRAM_COMMANDS.md` |
| Backtesting or performance metrics | `docs/BACKTESTING.md` |
| Risk management, position sizing | `docs/RISK_MANAGEMENT.md` |
| Docker, CI/CD, deployment | `docs/DEPLOYMENT.md` |
| FreqAI ML strategies | `docs/freqai_integration.md` |

This prevents duplicating work that has already been done and ensures changes are consistent with the existing architecture.

### 2 — Update Docs After Significant Changes

After completing a task, update the relevant documentation file if the change falls into any of these categories:

| Change type | Update which doc |
|---|---|
| New module, class, or major feature | `docs/ARCHITECTURE.md` (component table + data-flow if needed) |
| New or modified config key in `config.json` | `docs/CONFIGURATION.md` |
| New strategy or changes to signal logic | `docs/STRATEGIES.md` |
| New or modified REST/WebSocket endpoint | `docs/API_REFERENCE.md` |
| New or modified Telegram command | `docs/TELEGRAM_COMMANDS.md` |
| Changes to backtest engine or metrics | `docs/BACKTESTING.md` |
| Changes to risk rules, position sizing, circuit breakers | `docs/RISK_MANAGEMENT.md` |
| Changes to Docker, CI/CD workflow, or startup | `docs/DEPLOYMENT.md` |
| Changes to FreqAI ML strategies or feature engineering | `docs/freqai_integration.md` |

**What counts as significant:** new public API, changed config keys, new Telegram commands, new strategies, changed risk parameters, changed deployment steps. Refactors and bug fixes that don't change external behaviour do not require doc updates.

---



### Running the Bot
```bash
# Start with default configuration (via Makefile)
make run

# Start with custom strategy
python start_bot.py --strategy freqai_ml_freqai

# Start in testnet mode
make run-testnet
# or
python start_bot.py --testnet

# Start with debug logging
python start_bot.py --debug

# Run specific FreqAI strategies
make run-freqai-ml           # ML-based FreqAI strategy
make run-freqai-example      # Example FreqAI strategy
make run-strategy STRATEGY=momentum  # Any strategy by name
```

### Testing
```bash
# Run all tests (via Makefile)
make test

# Run with coverage
make test-cov

# Run specific test file
python -m pytest tests/test_strategies.py -v

# Run all quality checks
make check        # Runs lint, type-check, and tests
make check-cov    # Same as check but with coverage
```

### Code Quality
```bash
# Format code
make format

# Lint code
make lint

# Type checking
make type-check

# Clean cache files
make clean
```

### Environment Setup
```bash
# Install dependencies
make install

# Install development dependencies
make install-dev
```

## Architecture

### Core Components

1. **Main Bot (`src/bot.py`)** - The `AkibaCryptoBot` class orchestrates the entire trading system:
   - Manages the main trading cycle (runs every 60 seconds)
   - Coordinates exchange interactions, strategy execution, and risk management
   - Handles signal handlers for graceful shutdown (SIGINT/SIGTERM)
   - Integrates with Telegram for notifications and remote commands

2. **Startup Script (`start_bot.py`)** - Entry point with CLI argument parsing:
   - Supports strategy override (`--strategy`)
   - Testnet mode (`--testnet`)
   - Debug logging (`--debug`)
   - Model training for FreqAI (`--train-model`)

3. **Exchange Layer (`src/exchange/binance_client.py`)** - Handles all Binance API interactions:
   - Market data fetching (OHLCV, tickers, order book)
   - Order management (place, cancel, status)
   - Account information and balances
   - Supports both sandbox and live trading modes via ccxt library

4. **Strategy Framework (`src/strategies/`)** - Modular trading strategy system with two main hierarchies:

   **Base Strategy System:**
   - `base_strategy.py` contains `BaseStrategy` (abstract base class) and `StrategyManager`
   - `StrategyManager` handles dynamic strategy loading and switching
   - Strategies must implement `generate_signal()` and `calculate_indicators()`
   - Signal types: BUY, SELL, HOLD, CLOSE_LONG, CLOSE_SHORT
   - Built-in performance tracking (win rate, total PnL, signal counts)

   **FreqAI Integration (`src/strategies/freqai/`):**
   - `base_freqai_strategy.py` - Base class for ML-powered strategies
   - `example_freqai_strategy.py` - Example FreqAI implementation
   - `ml_freqai_strategy.py` - Production ML strategy
   - Supports feature engineering, model training, and prediction pipelines
   - Compatible with LightGBM, XGBoost, CatBoost, scikit-learn models

5. **Risk Management (`src/risk_management.py`)** - Position sizing, stop-loss, take-profit calculations, portfolio risk controls, and signal strength-based position adjustments.

6. **Technical Indicators (`src/indicators/technical_indicators.py`)** - Wrapper for technical analysis calculations used by strategies.

7. **Notifications (`src/notifications/telegram_bot.py`)** - Telegram bot integration:
   - Trade alerts (entry/exit notifications)
   - Bot status commands (`/status`, `/balance`, `/trades`)
   - Remote control (`/stop`, `/resume`)
   - Error notifications

8. **Logging System (`src/bot_logging/logger.py`)** - Structured logging with file rotation and configurable levels.

9. **Backtesting (`src/backtesting/`)** - Historical strategy testing:
   - `engine.py` - Backtesting engine
   - `backtest_runner.py` - Runner for batch backtests

### Key Design Patterns

- **Async/Await**: All exchange operations and main loops use async patterns for non-blocking I/O
- **Strategy Pattern**: Strategies inherit from BaseStrategy and can be swapped dynamically via StrategyManager
- **Dynamic Loading**: StrategyManager.add_strategy_by_name() loads strategies by string name at runtime
- **Signal-Based Trading**: Strategies generate TradingSignal objects with strength (0.0-1.0), price, timestamps, and metadata
- **Configuration-Driven**: All settings managed through config/config.json and environment variables
- **CLI Override**: Command-line arguments can override config file settings (strategy, testnet mode, etc.)

### Trading Cycle Flow

The bot's main loop (`trading_cycle()` in bot.py) executes every 60 seconds:

1. **Update Market Data** - Fetch latest prices for all configured pairs
2. **Manage Positions** - Check existing trades for stop-loss/take-profit/exit conditions
3. **Scan Opportunities** - For each pair:
   - Skip if already in position or max trades reached
   - Fetch OHLCV data (200 candles for indicator calculation)
   - Call `strategy_manager.generate_signal()`
   - Validate signal strength and risk conditions
   - Calculate position size using `risk_manager.calculate_position_size_with_signal()`
   - Place trade if all conditions met

### Configuration Structure

The main configuration (`config/config.json`) includes:
- **exchange**: Binance settings, sandbox mode, rate limits
- **trading**: Pairs, timeframes, strategy selection, max open trades
- **risk_management**: Position sizes, stop-loss/take-profit percentages, trailing stops
- **freqai**: ML model configuration, training parameters, feature engineering settings
- **notifications**: Telegram preferences
- **logging**: Log levels, file paths, rotation settings
- **backtesting**: Time ranges, initial balance, fee structure

## Important Implementation Notes

### Environment Variables
- All sensitive credentials must be in `.env` file, never hardcoded
- Required: `BINANCE_API_KEY`, `BINANCE_SECRET_KEY`
- Optional: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

### Security and Trading Safety
- Bot defaults to sandbox mode (`config.exchange.sandbox = true`)
- Always test new strategies in sandbox first
- API keys should have minimal required permissions
- Never commit `.env` files or credentials to git

### Strategy Development

**Creating a New Standard Strategy:**
1. Inherit from `BaseStrategy` in `src/strategies/base_strategy.py`
2. Implement required methods:
   - `async generate_signal(symbol, ohlcv_data, current_price)` - Return TradingSignal or None
   - `calculate_indicators(ohlcv_data)` - Return DataFrame with technical indicators
3. Set signal strength (0.0 to 1.0) - affects position sizing
4. Optionally override `should_exit_position()` for custom exit logic
5. Add to StrategyManager.add_strategy_by_name() for dynamic loading

**Creating a FreqAI Strategy:**
1. Inherit from `BaseFreqAIStrategy` in `src/strategies/freqai/base_freqai_strategy.py`
2. Implement FreqAI-specific methods:
   - `feature_engineering_expand_all()` - Multi-timeframe features
   - `feature_engineering_standard()` - Single timeframe features
   - `set_freqai_targets()` - Define prediction targets
   - `populate_indicators()` - Add indicators to dataframe
3. Update config.json with freqai settings
4. Add to StrategyManager for loading

### Strategy Loading System

The `StrategyManager` in `base_strategy.py` handles strategy lifecycle:
- Strategies are loaded by name string (e.g., "momentum", "freqai_ml_freqai")
- `add_strategy_by_name()` uses if/elif logic to map names to classes
- When adding a new strategy, update this method to include your strategy
- Active strategy is set via `set_active_strategy(name)`
- CLI can override config: `python start_bot.py --strategy your_strategy_name`

### Testing

The codebase includes a comprehensive test suite in `tests/`:
- `test_strategies.py` - Strategy signal generation and validation
- `test_binance_connector.py` - Exchange client testing (mocked)
- `test_config.py` - Configuration loading
- `test_logger.py` - Logging system
- `test_freqai_strategies.py` - FreqAI strategy tests
- Use pytest fixtures from `conftest.py` for common test data
- All async tests require pytest-asyncio
- Mock external API calls to avoid hitting rate limits

### Error Handling
- All exchange operations wrapped in try/catch with logging
- Graceful shutdown on SIGINT/SIGTERM signals
- Bot continues running despite individual operation failures
- Critical errors trigger Telegram notifications
- Trading cycle errors are logged but don't stop the bot
