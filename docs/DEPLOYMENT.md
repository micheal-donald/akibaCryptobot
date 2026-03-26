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

### 5. Apply a Reviewed Deployment Profile

Paper rollout promotion should be done locally and reviewed in git before the pinned SHA is deployed. Use the profile helper to keep the explicit compatibility fields synchronized:

```bash
python scripts/set_runtime_profile.py paper_shadow
git diff -- config/config.json
```

Promotion path:

1. Run `python scripts/set_runtime_profile.py paper_shadow`.
2. Review the diff in `config/config.json`.
3. Repeat with `paper_runtime_entries` or `paper_runtime_primary` when the staged rollout is ready.
4. Deploy the reviewed commit at a pinned SHA.

Rollback path:

1. Run `python scripts/set_runtime_profile.py paper_legacy`.
2. Review the diff in `config/config.json`.
3. Deploy the reviewed rollback commit at the pinned SHA you want to restore.

The helper only mutates the local config file. It does not add a server-side config mutator, and it keeps the deployment profile, runtime mode, and shadow compatibility flag aligned so the resulting change is easy to audit.

The direct deploy helper, `scripts/deploy_snapshot.sh`, can optionally verify the post-restart status payload before it prints success output:

```bash
bash scripts/deploy_snapshot.sh \
  --expected-profile paper_shadow \
  --expected-effective-mode shadow
```

When either flag is supplied, deploy verification fails if `/api/status` does not report the expected `configured_deployment_profile` or `effective_runtime_mode`. If neither flag is supplied, the helper keeps the previous health-only deploy behavior.

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
Test → Smoke Test
```

| Job | Steps |
|---|---|
| **test** | Install deps → pytest with coverage → Upload to Codecov |
| **smoke** | Install deps → Verify app starts → Check routes registered |

### 2. `deploy.yml` — Continuous Deployment

**Triggers:** `workflow_run` after `CI` succeeds on `master`

```
Checkout exact CI-passed SHA → Rsync app files → Render runtime `.env`
→ Install deps only when `requirements.txt` changes → Install/version the systemd unit
→ Restart service → Wait for `/api/health` → Notify Telegram
```

### 3. `train.yml` — Model Training

Handles FreqAI model re-training on schedule or manual trigger.

### GitHub Secrets Required

| Secret | Description |
|---|---|
| `SERVER_IP` | Vultr VPS IP |
| `SSH_PRIVATE_KEY` | SSH private key for root access |
| `TRADING_MODE` | GitHub Actions variable for `paper` or `live` mode |
| `BINANCE_API_KEY` | Binance API key |
| `BINANCE_SECRET_KEY` | Binance secret key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |

---

### Runtime vs Deployment Secrets

Application runtime secrets live in the server-side `.env` file and are rendered by the deploy workflow from GitHub Actions secrets and variables. Deployment metadata such as `SERVER_IP` belongs in GitHub Actions configuration, not in the local project `.env`.

### Paper Activation Procedure

Phase 8 paper rollout is intentionally staged. Use the explicit runtime mode in `config/config.json` and promote one step at a time.

Recommended activation path:

1. **Start in `legacy`**
   - Keep `runtime.runtime_mode` set to `legacy` while the classic loop remains authoritative.
   - Verify `/api/status` and `/ws/status` report:
     - `runtime_mode: legacy`
     - `rollout_health: legacy`
     - `rollout_recommended_action: stay_legacy`

2. **Move to `shadow`**
   - Set `runtime.runtime_mode` to `shadow`.
   - Redeploy the pinned SHA.
   - Verify the operator surfaces report:
     - `runtime_mode: shadow`
     - `state_source: shadow`
     - `rollout_health: shadow_ready`
     - `rollout_recommended_action: continue_shadow`
   - Stay here until the runtime projections match the legacy view closely enough for promotion.

3. **Promote to `runtime_entries`**
   - Set `runtime.runtime_mode` to `runtime_entries`.
   - Redeploy the pinned SHA.
   - Verify:
     - `runtime_owns_entries: true`
     - `runtime_owns_exits: false`
     - `rollout_health: promotion_ready`
     - `rollout_recommended_action: promote_runtime_primary`
   - At this stage the runtime owns new entries while legacy exit handling may still remain available for compatibility.

4. **Promote to `runtime_primary`**
   - Set `runtime.runtime_mode` to `runtime_primary`.
   - Redeploy the pinned SHA.
   - Verify:
     - `runtime_owns_entries: true`
     - `runtime_owns_exits: true`
     - `rollout_health: promotion_ready`
     - `rollout_recommended_action: stay_runtime_primary`

Rollback path:

1. Set `runtime.runtime_mode` back to `legacy`.
2. Redeploy the pinned SHA.
3. Confirm the operator surfaces return to:
   - `runtime_mode: legacy`
   - `rollout_health: legacy`
   - `rollout_recommended_action: stay_legacy`

Rollback should be the first response if runtime health degrades, rollout summaries report `rollback_recommended`, or operator verification fails after promotion.

For Phase 9, prefer the reviewed deployment profile helper above instead of editing these fields individually. The same rollout order applies, but the profile helper keeps the config explicit and makes the diff easier to review before deployment.

> [!IMPORTANT]
> Live exchange deployments keep the legacy loop as the safe default. If `exchange.sandbox` is `false`, runtime-owned modes (`shadow`, `runtime_entries`, `runtime_primary`) will fail fast unless `runtime.allow_live_runtime=true` is set explicitly. `live_legacy` remains allowed without the override.

### Vultr Bare-Metal Setup
The bot runs as a systemd service (`akibabot.service`) on a Vultr Ubuntu instance.

The canonical service unit is versioned in:

```text
deploy/systemd/akibabot.service
```

The service boot command is driven by:

```text
scripts/run_bot_service.sh
```

This keeps the deployed `ExecStart` aligned with `TRADING_MODE`, strategy, host, and port settings.

```bash
# SSH into the instance
ssh root@<SERVER_IP>

# Logs and Status
systemctl status akibabot.service
tail -f /opt/akibabot/logs/akiba_crypto_bot.log
```

### Manual Restart
```bash
ssh root@<SERVER_IP> "systemctl restart akibabot"
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
