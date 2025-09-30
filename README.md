# akibaCryptobot

An automated cryptocurrency trading bot with support for multiple strategies, risk management, and exchange connectors.

## Features

- Multiple trading strategies (Momentum, etc.)
- Risk management with stop-loss and take-profit
- Exchange connectors (Binance, etc.)
- Technical indicators calculation
- Portfolio management
- Telegram notifications
- Backtesting capabilities
- Comprehensive test suite

## Prerequisites

- Python 3.8 or higher
- pip (Python package installer)

## Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd akibaCryptobot
   ```

2. Create a virtual environment (optional but recommended):
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   make install
   # or
   pip install -r requirements.txt
   ```

## Configuration

1. Copy the example configuration file:
   ```bash
   cp config/config.json.example config/config.json
   ```

2. Edit `config/config.json` with your settings:
   - Exchange API keys
   - Trading pairs
   - Strategy parameters
   - Risk management settings

3. Create a `.env` file with sensitive information:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` with your actual API keys and secrets.

## Usage

### Running the bot

```bash
make run
# or
python -m src.akiba_crypto_bot
```

### Running in testnet mode (if supported by exchange)

```bash
make run-testnet
# or
python -m src.akiba_crypto_bot --testnet
```

## Testing

### Run all tests

```bash
make test
# or
python -m pytest tests/ -v
```

### Run tests with coverage

```bash
make test-cov
# or
python -m pytest tests/ --cov=src --cov-report=html --cov-report=term
```

### Run specific test file

```bash
python -m pytest tests/test_strategies.py -v
```

## Development

### Install development dependencies

```bash
make install-dev
# or
pip install -r requirements-dev.txt
```

### Code formatting

```bash
make format
# or
black src/ tests/
```

### Linting

```bash
make lint
# or
flake8 src/ tests/
```

### Type checking

```bash
make type-check
# or
mypy src/ tests/
```

### Run all checks

```bash
make check
# or for coverage as well
make check-cov
```

## Project Structure

```
akibaCryptobot/
├── config/              # Configuration files
├── docs/                # Documentation
├── logs/                # Log files
├── src/                 # Source code
│   ├── connectors/      # Exchange connectors
│   ├── indicators/      # Technical indicators
│   ├── logger/          # Logging module
│   ├── portfolio/       # Portfolio management
│   ├── risk_management/ # Risk management
│   ├── strategies/      # Trading strategies
│   └── akiba_crypto_bot.py # Main bot module
├── tests/               # Test files
├── .env.example         # Example environment variables
├── .gitignore           # Git ignore file
├── Makefile             # Makefile for common tasks
├── pytest.ini           # Pytest configuration
├── requirements.txt     # Production dependencies
├── requirements-dev.txt # Development dependencies
└── README.md            # This file
```

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature-name`
3. Commit your changes: `git commit -am 'Add new feature'`
4. Push to the branch: `git push origin feature-name`
5. Create a pull request

## License

MIT License

## Disclaimer

This software is provided for educational purposes only. Trading cryptocurrencies involves substantial risk of loss. Do not trade with money you cannot afford to lose. The authors are not responsible for any losses incurred while using this software.