# AkibaCryptoBot Documentation

Welcome to the AkibaCryptoBot documentation. Use the guides below to understand, configure, deploy, and extend the bot.

---

## Guides

| Document | Description |
|---|---|
| [Architecture](ARCHITECTURE.md) | System overview, component diagram, trading cycle walkthrough, directory structure |
| [Configuration](CONFIGURATION.md) | All config options (`config.json`, `.env`) with types, defaults, and descriptions |
| [Strategies](STRATEGIES.md) | Strategy system, built-in strategies (Momentum, Ensemble, FreqAI ML), creating custom strategies |
| [Risk Management](RISK_MANAGEMENT.md) | Position sizing, stop loss, trailing stops, circuit breakers, correlation analysis |
| [API Reference](API_REFERENCE.md) | REST endpoints, WebSocket streams, request/response formats |
| [Telegram Commands](TELEGRAM_COMMANDS.md) | Bot commands, setup guide, automatic notifications |
| [Backtesting](BACKTESTING.md) | Backtest engine, walk-forward analysis, interpreting results |
| [Reconciliation](ARCHITECTURE.md#reconciliation) | How the bot stays in sync with actual exchange balances |
| [Deployment](DEPLOYMENT.md) | Local dev, Docker, CI/CD pipeline, cloud deployment |

---

## Quick Start

```bash
# 1. Install
git clone https://github.com/micheal-donald/akibaCryptobot.git
cd akibaCryptobot
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with your Binance API keys

# 3. Run (testnet by default)
python start_bot.py --testnet

# 4. Access dashboard
open http://localhost:8000
```

---

## Additional Resources

| Document | Description |
|---|---|
| [FreqAI Integration](freqai_integration.md) | FreqAI ML framework integration — feature engineering, custom strategies, model persistence |
