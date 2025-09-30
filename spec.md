# PROJECT: akibaCryptobot

## GOAL
End-to-end autonomous cryptocurrency trading bot with high performance and reliability

## CONSTRAINTS
- **Exchange**: Binance Spot (paper → live trading)
- **Trading Pairs**: BTC/USDT, ETH/USDT, and other major USDT pairs
- **Strategy**: Multi-strategy approach with technical indicators
- **Testing**: 100% test coverage with pytest + code quality checks
- **Security**: Environment variables only (.env) - no hardcoded secrets
- **Deployment**: Containerized with Docker
- **Performance Targets**: 
  - Profit factor ≥ 1.2
  - Max drawdown ≤ 15%
- **Task limit**: 2 hours per micro-task

## DELIVERABLES

### 1. Core Configuration
- `config/config.json` - Main bot configuration (Binance settings, trading pairs)
- `.env.example` - Template for environment variables
- `docker-compose.yml` - Development environment
- `docker-compose.prod.yml` - Production environment

### 2. Trading Engine
- `src/bot.py` - Main trading bot orchestrator
- `src/exchange/binance_client.py` - Binance API integration
- `src/strategies/` - Trading strategy implementations
- `src/indicators/` - Technical analysis indicators
- `src/risk_management.py` - Position sizing and risk controls

### 3. Strategy Framework
- `src/strategies/base_strategy.py` - Abstract strategy interface
- `src/strategies/momentum_strategy.py` - Momentum-based trading
- `src/strategies/mean_reversion.py` - Mean reversion strategy
- `src/backtesting/engine.py` - Strategy backtesting framework

### 4. Monitoring & Alerts
- `src/notifications/telegram_bot.py` - Telegram integration
- `src/monitoring/metrics.py` - Performance tracking
- `src/logging/logger.py` - Structured logging

### 5. Testing & Quality
- `tests/test_strategies.py` - Strategy unit tests
- `tests/test_exchange.py` - Exchange integration tests
- `tests/test_bot.py` - Main bot tests
- `.github/workflows/ci.yml` - Continuous integration

### 6. Environment Variables
- `BINANCE_API_KEY` - Binance API key
- `BINANCE_SECRET_KEY` - Binance secret key
- `TELEGRAM_BOT_TOKEN` - Telegram bot token
- `TELEGRAM_CHAT_ID` - Telegram chat ID for notifications
- `TRADING_MODE` - paper/live trading mode

## SUCCESS CRITERIA

### Performance Metrics
- Backtesting profit factor ≥ 1.2 over 1-year period
- Maximum drawdown ≤ 15%
- Sharpe ratio ≥ 1.5
- Win rate ≥ 55%

### Operational Requirements
- Bot runs 24/7 without crashes in paper trading mode
- Telegram bot responds to `/start`, `/status`, `/balance`, `/trades` commands
- All tests pass with 100% coverage
- Code quality checks pass (flake8, black, mypy)

### Risk Management
- Position sizing based on account balance percentage
- Stop-loss and take-profit mechanisms
- Maximum daily loss limits
- Emergency stop functionality

### Monitoring
- Real-time performance tracking
- Trade execution logging
- Error handling and recovery
- Performance alerts via Telegram

## ARCHITECTURE

```
akibaCryptobot/
├── src/
│   ├── bot.py                 # Main trading orchestrator
│   ├── exchange/              # Exchange integrations
│   ├── strategies/            # Trading strategies
│   ├── indicators/            # Technical indicators
│   ├── risk_management.py     # Risk controls
│   ├── notifications/         # Alert systems
│   └── monitoring/            # Performance tracking
├── config/                    # Configuration files
├── tests/                     # Test suite
├── docs/                      # Documentation
├── docker-compose.yml         # Development environment
└── requirements.txt           # Python dependencies
```

## DEVELOPMENT PHASES

1. **Foundation** - Project setup, basic structure, exchange connection
2. **Strategy Development** - Implement core trading strategies
3. **Risk Management** - Position sizing, stop-loss, portfolio management
4. **Monitoring** - Telegram integration, logging, metrics
5. **Testing** - Comprehensive test suite, backtesting
6. **Deployment** - Containerization, CI/CD pipeline