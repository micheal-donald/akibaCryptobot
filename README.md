# 🤖 AkibaCryptoBot HFT

[![CI](https://github.com/micheal-donald/akibaCryptobot/actions/workflows/ci.yml/badge.svg)](https://github.com/micheal-donald/akibaCryptobot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

AkibaCryptoBot is a state-of-the-art **High-Frequency Trading (HFT)** engine optimized for low-latency execution, real-time data streaming, and machine-learning-powered decision making. Built for both **Binance Spot and Futures** markets.

---

## ⚡ Key HFT Upgrades

- **Real-time WebSocket Streaming**: Zero-latency price and order book updates via `BinanceSocketManager`.
- **Ensemble Decision Engine**: Weighted consensus voting between traditional Momentum TA (40%) and FreqAI ML (60%).
- **Optimized ML Core**: Upgraded FreqAI integration using **LightGBM** for 10x faster training and sub-millisecond predictions.
- **Futures Support**: Advanced control for USDT-M Futures including isolated margin and custom leverage (up to 3x recommended).
- **Tokyo Edge Deployment**: Automated Terraform IaC for deployment to AWS Tokyo (ap-northeast-1) for minimal latency to Binance servers.

---

## 🛠 Features

### 📡 Data & Execution
- **WebSocket Manager**: Connection pooling and auto-reconnect for uninterrupted data streams.
- **Multi-Market Engine**: Seamlessly switch between Spot and Futures trading.
- **Asyncio Architecture**: Non-blocking I/O for handling hundreds of updates per second.

### 🧠 Intelligence
- **Ensemble Strategy**: Combines technical indicators with ML predictions for robust signals.
- **FreqAI Integration**: Continuous learning and feature engineering using technical indicators.
- **LightGBM Optimization**: Model training optimized for high-frequency execution.

### 🛡 Risk Management
- **Circuit Breakers**: Automatic halting if market volatility or drawdowns exceed thresholds.
- **Leverage Control**: Strict margin management for Futures.
- **Real-time Monitoring**: Telegram notifications for every trade, error, and performance update.

---

## 🚀 Quick Start

### 1. Local Installation
```bash
git clone https://github.com/micheal-donald/akibaCryptobot.git
cd akibaCryptobot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration
Copy `.env.example` to `.env` and configure your API keys:
```bash
BINANCE_API_KEY=your_key
BINANCE_SECRET_KEY=your_secret
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 3. Run the Bot
```bash
# Run with the new Ensemble HFT strategy
python start_bot.py --strategy ensemble --testnet
```

---

## 🏗 Deployment (CI/CD)

The bot is designed to run in a low-latency environment. We use **Terraform** for infrastructure and **GitHub Actions** for automated deployment.

### 1. Provision Infrastructure (AWS Tokyo)
```bash
cd terraform
terraform init
terraform apply
```

### 2. Automated Deployment
This repo includes a full CI/CD pipeline:
- **CI**: Runs linting, type checks, and unit tests on every push.
- **CD**: Automatically builds a Docker image, pushes to GHCR, and deploys to your AWS EC2 instance.

For detailed steps, see [**Deployment Guide**](docs/DEPLOYMENT.md).

---

## 📊 Strategies

| Strategy | Type | Accuracy | Latency |
|----------|------|----------|---------|
| **Ensemble** | Hybrid (TA+ML) | ⭐⭐⭐⭐⭐ | Ultra-Low |
| **FreqAI ML** | Pure ML | ⭐⭐⭐⭐ | Low |
| **Momentum** | Pure TA | ⭐⭐⭐ | Minimal |

---

## 📂 Project Structure

```text
├── .github/workflows/   # CI/CD Pipelines (Test & Deploy)
├── terraform/           # Infrastructure as Code (AWS Tokyo)
├── src/
│   ├── exchange/        # WebSocket Manager & Binance Client
│   ├── strategies/      # HFT & Ensemble Logic
│   ├── risk_management/ # Circuit breakers & Margin control
│   └── bot.py           # Core Orchestrator
├── Dockerfile           # Multi-stage optimized image
└── docker-compose.yml   # Production stack
```

---

## 🧪 Testing

```bash
# Run unit tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=src
```

---

## ⚠️ Disclaimer

This bot is for **educational purposes only**. Trading cryptocurrencies involves significant risk. The HFT nature of this bot can lead to rapid gains or losses. **Always test on Testnet/Paper Trading mode first.**

---

## 📜 License

MIT License. See [LICENSE](LICENSE) for details.
