# Deployment Guide

This guide covers local development, Docker deployment, and CI/CD pipeline configuration.

---

## Prerequisites

- Python 3.10+
- Docker & Docker Compose (for containerized deployment)
- Binance API credentials
- Telegram bot token (optional, for notifications)

---

## Local Development

### 1. Clone & Install

```bash
git clone https://github.com/micheal-donald/akibaCryptobot.git
cd akibaCryptobot

python -m venv .venv
source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Run

```bash
# Bot + API server (default port 8000)
python start_bot.py

# With options
python start_bot.py --testnet --debug --port 8080

# Bot only (no API server)
python start_bot.py --no-api

# Override strategy
python start_bot.py --strategy momentum
```

**CLI Options:**

| Flag | Description |
|---|---|
| `--config PATH` | Config file path (default: `config/config.json`) |
| `--strategy NAME` | Override strategy (`momentum`, `ensemble`, `freqai_ml_freqai`) |
| `--testnet` | Force testnet mode (paper trading) |
| `--debug` | Enable debug logging |
| `--dry-run` | Dry-run mode (no trades) |
| `--port PORT` | API server port (default: 8000) |
| `--host HOST` | API server host (default: 0.0.0.0) |
| `--no-api` | Run trading bot without the API server |

### 4. Access

- **Dashboard:** http://localhost:8000
- **API docs:** http://localhost:8000/docs
- **Health check:** http://localhost:8000/api/health

---

## Docker Deployment

### Dockerfile

The project uses a multi-stage build for smaller images:

```
Stage 1 (builder): Install Python dependencies with gcc/g++
Stage 2 (runtime): Copy only the installed packages + app code
                    Runs as non-root user (botuser)
```

### Build & Run

```bash
# Build
docker build -t akibacryptobot .

# Run
docker run -d \
  --name akiba-cryptobot \
  --env-file .env \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/models:/app/models \
  -p 8000:8000 \
  akibacryptobot
```

### Docker Compose (recommended)

```bash
# Start
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down

# Rebuild after code changes
docker-compose up -d --build
```

#### Environment Variables in Docker Compose

| Variable | Description | Default |
|---|---|---|
| `TRADING_MODE` | `paper` or `live` | `paper` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `BINANCE_API_KEY` | Binance API key | — |
| `BINANCE_SECRET_KEY` | Binance API secret | — |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | — |
| `TELEGRAM_CHAT_ID` | Telegram chat ID | — |
| `MAX_POSITION_SIZE` | Max portfolio fraction per trade | `0.05` |
| `MAX_DAILY_LOSS` | Max daily loss fraction | `0.02` |

#### Persistent Volumes

| Mount | Purpose |
|---|---|
| `./logs:/app/logs` | Log files |
| `./data:/app/data` | SQLite trade database |
| `./models:/app/models` | Trained ML models |

#### Resource Limits

The `docker-compose.yml` sets:
- **CPU limit:** 1.0 core
- **Memory limit:** 1 GB
- **CPU reservation:** 0.25 core
- **Memory reservation:** 256 MB

---

## CI/CD Pipeline

Three GitHub Actions workflows in `.github/workflows/`:

### 1. `ci.yml` — Continuous Integration

**Triggers:** Push to `master`, `develop`, `feature/**`; PRs to `master`

```
Lint & Format → Test → Smoke Test
```

| Job | Steps |
|---|---|
| **lint** | Install deps → Black check → Flake8 → MyPy |
| **test** | Install deps → pytest with coverage → Upload to Codecov |
| **smoke** | Install deps → Verify app starts → Check routes registered |

### 2. `deploy.yml` — Continuous Deployment

**Triggers:** Push to `master` (after CI passes)

```
Build Docker → Push to GHCR → SSH to EC2 → Pull & Restart → Notify Telegram
```

### 3. `train.yml` — Model Training

Handles FreqAI model re-training on schedule or manual trigger.

### GitHub Secrets Required

| Secret | Description |
|---|---|
| `EC2_HOST` | EC2 public IP or DNS |
| `SSH_PRIVATE_KEY` | SSH private key for EC2 |
| `BINANCE_API_KEY` | Binance API key |
| `BINANCE_SECRET_KEY` | Binance secret key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |
| `TRADING_MODE` | `paper` or `live` |

---

## Cloud Deployment (EC2)

### First-Time Setup

```bash
# 1. Provision infrastructure
cd terraform
terraform init
terraform apply
# Note the public_ip from output

# 2. SSH into the instance
ssh -i ~/.ssh/akibabot-key.pem ubuntu@<public_ip>

# 3. Run the setup script
curl -sSL https://raw.githubusercontent.com/YOUR_REPO/master/scripts/setup-ec2.sh | bash

# 4. Add GitHub Secrets to your repository

# 5. Push to master to trigger deployment
git push origin master
```

### Manual Deployment

```bash
ssh -i ~/.ssh/akibabot-key.pem ubuntu@<ec2_ip>
cd /opt/akibabot
docker-compose pull
docker-compose up -d
```

---

## Troubleshooting

| Problem | Command |
|---|---|
| Container won't start | `docker-compose logs` |
| Check status | `docker-compose ps` |
| Restart | `docker-compose restart` |
| View bot logs | `tail -f logs/akiba_crypto_bot.log` |
| View trade logs | `tail -f logs/trades.log` |
| View error logs | `tail -f logs/errors.log` |
| Check DB | `sqlite3 data/trades.db "SELECT * FROM trades WHERE status='open'"` |
