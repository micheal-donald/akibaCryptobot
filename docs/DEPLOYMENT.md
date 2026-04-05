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

Recommended paper promotion and rollback flow:

1. Apply the reviewed profile locally with `python scripts/set_runtime_profile.py <profile>`.
2. Review and commit the resulting `config/config.json` change.
3. Deploy that reviewed commit at a pinned SHA with `scripts/deploy_snapshot.sh`.
4. Check `/api/health` for liveness and `/api/status` for profile/mode assertions.

The helper only mutates the local config file. It does not add a server-side config mutator, and it keeps the deployment profile, runtime mode, and shadow compatibility flag aligned so the resulting change is easy to audit.

The direct deploy helper, `scripts/deploy_snapshot.sh`, can verify the post-restart status payload before it prints success output. Pass the expected profile and effective mode for the deployment you are promoting:

```bash
bash scripts/deploy_snapshot.sh \
  --expected-profile paper_shadow \
  --expected-effective-mode shadow
```

When either flag is supplied, deploy verification fails if `/api/status` does not report the expected `configured_deployment_profile` or `effective_runtime_mode`. If neither flag is supplied, the helper keeps the previous health-only deploy behavior.

Snapshot deploys intentionally preserve server-side `models/` artifacts. The pinned code deploy is sourced from `git archive`, while trained FreqAI joblib files are runtime artifacts that may be produced outside git. Preserving `models/` keeps code deploys from wiping the currently active model set; model refresh remains a separate training/deployment step.

### Interpreting Runtime Telemetry

`/api/status` and `/ws/status` expose `runtime_telemetry` when the runtime is active. Use it to judge whether a rollout is genuinely healthy, not just whether the process is alive.

| Signal | Interpretation | Promotion impact |
|---|---|---|
| `latency.event_age_ms` rising steadily | The runtime is backing up behind its own ingest path | Block promotion until the trend stabilizes |
| `latency.exchange_age_ms` rising steadily | The feed is stale, delayed, or timestamp skewed | Block promotion until the feed path is clean |
| `latency.queue_lag_ms.<queue>` rising with `queue_depths.<queue>` | A hot-path queue is accumulating backlog | Block promotion; this is the clearest capacity warning |
| `metric_counts.dropped_event*` increasing | Runtime work is being discarded or skipped | Block promotion until drops stop |
| `metric_counts.reconnect*` increasing repeatedly | The runtime is repeatedly reconnecting a feed or transport | Investigate before promotion |
| `metric_counts.stale_symbol*` increasing | One or more symbols are going stale | Treat as a market-data problem before promotion |
| `runtime_start_pending=true` | The runtime is still warming up | Do not roll back yet unless it later turns into `rollback_recommended` |

Healthy paper rollout targets are:

- `paper_shadow`: `rollout_health=shadow_ready`, `rollout_recommended_action=continue_shadow`
- `paper_runtime_entries`: `rollout_health=promotion_ready`, `rollout_recommended_action=promote_runtime_primary`
- `paper_runtime_primary`: `rollout_health=promotion_ready`, `rollout_recommended_action=stay_runtime_primary`

If any of the following are true, treat the deployment as blocked and hold or roll back:

- `runtime_is_healthy=false`
- `rollout_health=rollback_recommended`
- `runtime_start_pending=true` after the startup window has elapsed
- queue depth and queue lag both trend upward over successive samples
- dropped-event counters continue to increase
- exchange-age latency grows unexpectedly or goes negative before filtering

#### Promotion and Rollback Runbook

Use the same command pattern for every paper profile. Replace `<SHA>` with the reviewed commit you intend to deploy.

| Step | Local profile command | Deploy command | Expected `/api/status` values |
|---|---|---|---|
| `paper_shadow` | `python scripts/set_runtime_profile.py paper_shadow` | `DEPLOY_SHA=<SHA> bash scripts/deploy_snapshot.sh --expected-profile paper_shadow --expected-effective-mode shadow` | `configured_deployment_profile=paper_shadow`, `effective_runtime_mode=shadow`, `effective_runtime_enabled=true`, `effective_runtime_owns_entries=false`, `effective_runtime_owns_exits=false`, `runtime_mode=shadow`, `state_source=shadow`, initial `rollout_health=startup_pending` / `rollout_recommended_action=wait_for_runtime`, then `rollout_health=shadow_ready` / `rollout_recommended_action=continue_shadow` once startup settles |
| `paper_runtime_entries` | `python scripts/set_runtime_profile.py paper_runtime_entries` | `DEPLOY_SHA=<SHA> bash scripts/deploy_snapshot.sh --expected-profile paper_runtime_entries --expected-effective-mode runtime_entries` | `configured_deployment_profile=paper_runtime_entries`, `effective_runtime_mode=runtime_entries`, `effective_runtime_enabled=true`, `effective_runtime_owns_entries=true`, `effective_runtime_owns_exits=false`, `runtime_mode=runtime_entries`, `state_source=runtime_projection`, `rollout_health=promotion_ready`, `rollout_recommended_action=promote_runtime_primary` |
| `paper_runtime_primary` | `python scripts/set_runtime_profile.py paper_runtime_primary` | `DEPLOY_SHA=<SHA> bash scripts/deploy_snapshot.sh --expected-profile paper_runtime_primary --expected-effective-mode runtime_primary` | `configured_deployment_profile=paper_runtime_primary`, `effective_runtime_mode=runtime_primary`, `effective_runtime_enabled=true`, `effective_runtime_owns_entries=true`, `effective_runtime_owns_exits=true`, `runtime_mode=runtime_primary`, `state_source=runtime_projection`, `rollout_health=promotion_ready`, `rollout_recommended_action=stay_runtime_primary` |
| `paper_legacy` rollback | `python scripts/set_runtime_profile.py paper_legacy` | `DEPLOY_SHA=<SHA> bash scripts/deploy_snapshot.sh --expected-profile paper_legacy --expected-effective-mode legacy` | `configured_deployment_profile=paper_legacy`, `effective_runtime_mode=legacy`, `effective_runtime_enabled=false`, `effective_runtime_owns_entries=false`, `effective_runtime_owns_exits=false`, `runtime_mode=legacy`, `state_source=legacy`, `rollout_health=legacy`, `rollout_recommended_action=stay_legacy` |

`/api/health` only proves the process is live. A successful health check does not prove the expected deployment profile or runtime mode. Treat the deploy as successful only when the health check passes and `/api/status` reports the expected profile and mode fields above.

#### First `paper_shadow` Promotion Runbook

Use this exact sequence for the first real paper promotion:

1. Apply the reviewed profile locally: `python scripts/set_runtime_profile.py paper_shadow`
2. Review and commit the config diff: `git diff -- config/config.json`, then `git add config/config.json && git commit -m "deploy: paper_shadow promotion"`
3. Deploy the reviewed commit at a pinned SHA: `DEPLOY_SHA=$(git rev-parse HEAD) bash scripts/deploy_snapshot.sh --expected-profile paper_shadow --expected-effective-mode shadow`
4. Verify `/api/status` reports these values:
   - `configured_deployment_profile=paper_shadow`
   - `effective_runtime_mode=shadow`
   - `effective_runtime_enabled=true`
   - `effective_runtime_owns_entries=false`
   - `effective_runtime_owns_exits=false`
   - `runtime_mode=shadow`
   - `state_source=shadow`
   - first: `runtime_start_pending=true`, `runtime_start_status=configured|starting`, `rollout_health=startup_pending`, `rollout_recommended_action=wait_for_runtime`
   - then, after startup completes: `runtime_start_pending=false`, `runtime_start_status=running`, `rollout_health=shadow_ready`, `rollout_recommended_action=continue_shadow`

Do not roll back while the runtime is still in `startup_pending`. Roll back only if startup settles and `/api/status` still reports `rollback_recommended` or another unexpected profile/mode value. If rollback is required, use the same pattern on `paper_legacy`:

```bash
python scripts/set_runtime_profile.py paper_legacy
git add config/config.json && git commit -m "deploy: paper_legacy rollback"
DEPLOY_SHA=$(git rev-parse HEAD) bash scripts/deploy_snapshot.sh --expected-profile paper_legacy --expected-effective-mode legacy
```

#### Live Canary Promotion Runbook

Use the same pinned-SHA deploy flow for live runtime activation, but keep the live override explicit in the reviewed config diff. The profile helper sets `deployment_profile`, `runtime_mode`, and `shadow_mode`; it does not silently enable live runtime ownership. For `live_shadow`, `live_runtime_entries`, and `live_runtime_primary`, the same reviewed commit must also contain `runtime.allow_live_runtime=true`.

Recommended live progression:

1. `live_legacy`
2. `live_shadow`
3. `live_runtime_entries`
4. `live_runtime_primary`
5. `live_legacy` rollback

Live canary commands:

| Step | Local config review | Deploy command | Expected `/api/status` highlights |
|---|---|---|---|
| `live_shadow` | `python scripts/set_runtime_profile.py live_shadow`, then review `runtime.allow_live_runtime=true` in the same diff | `DEPLOY_SHA=<SHA> bash scripts/deploy_snapshot.sh --expected-profile live_shadow --expected-effective-mode shadow` | `trading_mode=live`, `configured_deployment_profile=live_shadow`, `rollout_environment=live`, `rollout_stage=canary`, initial `rollout_recommended_action=wait_for_live_runtime`, then `rollout_recommended_action=promote_live_runtime_entries` |
| `live_runtime_entries` | `python scripts/set_runtime_profile.py live_runtime_entries`, keep `runtime.allow_live_runtime=true` | `DEPLOY_SHA=<SHA> bash scripts/deploy_snapshot.sh --expected-profile live_runtime_entries --expected-effective-mode runtime_entries` | `trading_mode=live`, `configured_deployment_profile=live_runtime_entries`, `rollout_environment=live`, `rollout_stage=canary`, `rollout_recommended_action=promote_live_runtime_primary` |
| `live_runtime_primary` | `python scripts/set_runtime_profile.py live_runtime_primary`, keep `runtime.allow_live_runtime=true` | `DEPLOY_SHA=<SHA> bash scripts/deploy_snapshot.sh --expected-profile live_runtime_primary --expected-effective-mode runtime_primary` | `trading_mode=live`, `configured_deployment_profile=live_runtime_primary`, `rollout_environment=live`, `rollout_stage=primary`, `rollout_recommended_action=stay_live_runtime_primary` |
| `live_legacy` rollback | `python scripts/set_runtime_profile.py live_legacy`, remove or set `runtime.allow_live_runtime=false` in the reviewed diff | `DEPLOY_SHA=<SHA> bash scripts/deploy_snapshot.sh --expected-profile live_legacy --expected-effective-mode legacy` | `trading_mode=live`, `configured_deployment_profile=live_legacy`, `rollout_environment=live`, `rollout_stage=legacy`, `rollout_recommended_action=stay_live_legacy` |

If any live canary or live primary rollout reports `rollout_health=rollback_recommended`, the expected immediate action is `rollback_to_live_legacy`.

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

Phase 10 makes the paper path explicit through reviewed deployment profiles. Use the same helper/deploy/status sequence shown above for every rollout step:

1. `paper_shadow`
2. `paper_runtime_entries`
3. `paper_runtime_primary`
4. `paper_legacy` rollback

Each step should be verified in two phases:

1. `GET /api/health` only confirms the service started.
2. `GET /api/status` confirms the intended profile and runtime mode actually came up.

For this phase, the profile/mode assertions are the source of truth. A healthy process with the wrong `configured_deployment_profile` or `effective_runtime_mode` is still a failed deploy.

Rollback should be the first response if runtime health degrades, rollout summaries report `rollback_recommended`, or the status payload does not match the expected profile/mode pair after promotion.

> [!IMPORTANT]
> Live exchange deployments keep the legacy loop as the safe default. If `exchange.sandbox` is `false`, runtime-owned modes (`shadow`, `runtime_entries`, `runtime_primary`) will fail fast unless `runtime.allow_live_runtime=true` is set explicitly. `live_legacy` remains allowed without the override.
>
> When a reviewed live profile is selected, startup logs now emit `Live runtime rollout profile=<profile> stage=<legacy|canary|primary> allow_live_runtime=<bool>` before the bot begins trading. If a live canary or primary runtime profile is refused, the startup error names the reviewed live profile directly so the rollback path is obvious from the logs.

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
