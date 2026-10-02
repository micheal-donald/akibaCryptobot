# Deployment Guide

This guide covers local development, Docker deployment, and CI/CD pipeline configuration.

---

## Production: Dokploy and GHCR

Production uses Dokploy v0.30.8 on the existing Vultr server. DNS is configured
separately after deployment. The administrator panel binds only to localhost:

```bash
ssh -i ~/.ssh/akibabot_vultr -L 3000:127.0.0.1:3000 root@66.42.32.233
# Open http://localhost:3000
```

The administrator email is `michaelmonyenye@gmail.com`. Initial credentials are
stored in `/etc/akibabot/dokploy-admin.json` on the server, readable only by root.
The deployment API key and application IDs live in `/etc/akibabot/dokploy.json`;
keep both files out of Git and logs.

On a push to master, CI runs tests and the app smoke check, builds the Docker
image, verifies imports inside that image, and publishes
`ghcr.io/micheal-donald/akibacryptobot:sha-<commit>`. Its `container-image`
artifact records the immutable digest. Deploy downloads that artifact, verifies
its commit, and uses the local Dokploy API over SSH to deploy the digest.

Required Actions secrets: `SERVER_IP`, `SSH_PRIVATE_KEY`, and
`GHCR_PULL_TOKEN` (classic GitHub PAT with `read:packages`). The CI publisher
uses its built-in `GITHUB_TOKEN` with `packages:write`. The pull credential is
passed over SSH on stdin and saved through Docker login in Dokploy's persistent
Docker configuration volume. Renew that token before expiry.

The raw Compose specification is `deploy/dokploy/compose.yml`. It runs one
paper/testnet bot with a 1 GiB memory limit and mounts the existing
`/opt/akibabot/{data,logs,models,models_staging}` directories. Server `.env`
settings are copied into Dokploy's generated environment file. Runtime data and
models are excluded from images. The dashboard remains on port 8000 until DNS
and HTTPS are configured.

The first deployment pulls the image before stopping systemd, disables
`akibabot.service`, backs up root's crontab, and removes its old bot watchdog
and model-training jobs. Bind directories become writable by container UID
1000. API health and the running image must match before deployment succeeds.
A failed first migration stops the container before restoring systemd and cron;
a failed subsequent deployment restores the previous image. Keep the old
systemd checkout available until the migration has been verified.

Daily model training remains in GitHub Actions. It uploads models to the shared
host directory and restarts the existing container through
`scripts/dokploy_deploy.py --restart`. Training and deployment share a workflow
concurrency group and a server lock. They cannot revive the old systemd bot.

```bash
docker logs --tail 100 akiba-cryptobot
docker inspect -f '{{.Config.Image}} {{.State.Health.Status}}' akiba-cryptobot
curl -f http://127.0.0.1:8000/api/health
```

Uvicorn allows existing API connections 15 seconds to drain before cancelling
them, so persistent dashboard streams cannot indefinitely block bot shutdown.
The container allows 120 seconds for connection drain and bot cleanup.

Docker restarts the process if it exits. A health check alone does not restart
an unhealthy container; inspect health failures in Dokploy. The application
exits when its trading task fails so normal restart supervision can recover it.

The bare-metal instructions below remain available for development and manual
rollback, and are no longer the normal production CI deployment path.

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

### Phase 15 Kill-Switch and Anomaly Runbook

This runbook defines the future halt and recovery expectations for the low-latency runtime and HFT operating model. It is a documentation contract, not a claim that the current platform is already HFT-class.

#### Immediate Halt Triggers

Treat the deployment as an immediate halt candidate if any of the following persist beyond a brief startup window:

- `runtime_is_healthy=false`
- `rollout_health=rollback_recommended`
- `latency.event_age_ms` keeps rising across successive samples
- `latency.exchange_age_ms` keeps rising unexpectedly or the feed repeatedly goes stale
- `latency.queue_lag_ms.<queue>` rises together with `queue_depths.<queue>`
- `metric_counts.dropped_event*` continues to increase
- `metric_counts.reconnect*` spikes repeatedly without stabilization
- the order book becomes stale or invalid because of missing deltas, gaps, or sequence breaks
- exchange acknowledgements, cancels, or fills stop arriving within the expected window
- the native shadow report shows persistent divergence in a hot-path field

These are signals that the hot path is no longer trustworthy enough for continued promotion or live HFT-style operation.

#### Operator Response

When a halt trigger appears:

1. Stop promotion and freeze any further rollout changes.
2. Capture `/api/health`, `/api/status`, `/api/model/status`, and the relevant logs for the incident timeline.
3. If the runtime owns entries or exits, follow the reviewed rollback profile flow instead of ad hoc manual changes.
4. If the order book is stale, invalidate it and wait for a clean, healthy rebuild before resuming.
5. If exchange acknowledgements stop or reconnects spike, hold the deployment until connectivity stabilizes and telemetry stops degrading.
6. If the native shadow report is divergent, keep the native path read-only and resolve the mismatch before any cutover.

#### Recovery After a Halt

Recovery is profile-driven and deliberate:

1. Identify the root cause: degraded latency, stale feed, exchange anomaly, or native-shadow divergence.
2. Resolve the underlying problem before changing modes again.
3. Return to the safest reviewed profile first, usually `paper_legacy` or `live_legacy` depending on environment.
4. Re-run the pinned deploy with the expected profile and mode assertions.
5. Confirm the runtime reports healthy rollout state and no degraded-latency or stale-book warnings.
6. Re-attempt promotion only after the system is stable again.

For future HFT operations, the kill-switch must be faster than the strategy can accumulate damage. That is a design target, not a statement about current runtime behavior.

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
| `microstructure.decision_latency.avg_ms` rising | In-process book evaluation is getting slower | Investigate CPU load; unlikely under normal trading; helps separate in-process cost from network latency |
| `microstructure.pipeline_latency.avg_ms` rising | Websocket-to-decision time is degrading | Usually indicates network jitter or kernel backlog; affects fill timing on tighter strategies |
| `microstructure.subscribe_failures` non-empty | Depth/trade stream subscribe failed for a symbol | Degrades research surface but non-critical; stream may retry on next start |
| `microstructure.evaluations` not increasing | The microstructure service has gone silent | Indicates websocket issue or service hang; usually a transient feed problem, investigate logs |

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

Before a reviewed live runtime profile is started, an operator must create an approval record through `POST /api/trading/live/approve`. Startup validates that approval and records a matching audit event. Rolling back or cancelling a live promotion should call `POST /api/trading/live/rollback` so the approval trail and the deploy trail stay aligned.

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

### Phase 18 Runtime-Primary Soak Checklist

Before any live canary work, keep `paper_runtime_primary` on the server long enough to prove restart and reconciliation correctness, not just process liveness.

Minimum runtime-primary soak checks:

1. Restart the reviewed SHA with `paper_runtime_primary` and wait for `/api/status` to move from `startup_pending` to:
   - `runtime_is_running=true`
   - `runtime_is_healthy=true`
   - `runtime_recovery.issue_count=0` unless a known carried-position issue is being investigated
2. Confirm `/api/status.runtime_recovery` is populated after startup. This is the runtime-owned recovery summary, not a legacy fallback.
3. Trigger `/api/reconcile` and confirm:
   - `source=runtime_projection`
   - `runtime_drift=[]`
   - `lost_trades=[]`
   - any seeded sandbox inventory appears under `inventory_only`, not `orphans`
4. If there are carried positions, verify:
   - `runtime_recovery.restored_symbols` matches the carried symbols
   - `projected_active_positions` matches the restored runtime positions
   - native protection state is either restored or explicitly listed in `runtime_recovery.unprotected_symbols`
5. Hold promotion if any of these are non-empty:
   - `runtime_recovery.orphaned_symbols`
   - `runtime_recovery.reconcile_error_symbols`
   - `runtime_drift`
   - `lost_trades`

Recommended pass criteria before Phase 19 / live approval work:

- at least one clean restart with `runtime_recovery.issue_count=0`
- `/api/reconcile` reports no actionable runtime drift
- runtime telemetry remains healthy through the soak window
- model status remains loaded and non-stale

### Phase 20 Live Runtime Execution Criteria

Phase 20 turns the reviewed live profiles into an explicit canary ladder. Use the same pinned-SHA deploy flow as paper rollout, but treat live promotion as a higher-risk operator process.

#### Stage Criteria

| Stage | Preflight | Soak Criteria | Rollback Criteria |
|---|---|---|---|
| `live_shadow` | `runtime_is_healthy=true`, `halt_state=none`, `active_critical_alert_count=0`, `training_job.can_start_training=false`, `rollout_health=startup_pending` or `shadow_ready` | `rollout_stage=canary`, `rollout_recommended_action=promote_live_runtime_entries` | Any sustained `rollback_recommended`, startup failure after warm-up, or live alert escalation |
| `live_runtime_entries` | `runtime_is_healthy=true`, `halt_state=none`, `active_critical_alert_count=0`, `training_job.can_start_training=false` | `rollout_stage=canary`, runtime owns entries, legacy exits remain stable, `rollout_recommended_action=promote_live_runtime_primary` | Any runtime-entry mismatch, alert escalation, or `rollback_recommended` |
| `live_runtime_primary` | `runtime_is_healthy=true`, `halt_state=none`, `active_critical_alert_count=0`, `training_job.can_start_training=false` | `rollout_stage=primary`, runtime owns entries and exits, `rollout_recommended_action=stay_live_runtime_primary` | Any runtime-primary drift, halt state, or `rollback_recommended` |

If `halt_state` is not `none`, live rollout should report `rollout_health=promotion_blocked` and a `rollout_recommended_action` of either `clear_halt_before_promotion` or `clear_halt_before_continuing_live_primary`, depending on the live stage.

#### Live Rollout Rules

1. Do not promote if `halt_state` is not `none`.
2. Do not promote if `training_job` reports an active queue or running job.
3. Do not promote if `/api/status` or `/ws/status` disagree on profile, stage, or rollout action.
4. Do not treat `runtime_start_pending=true` as a rollback signal; wait for startup to settle first.
5. Roll back to `live_legacy` immediately if the reviewed live stage reports `rollback_recommended` after warm-up.
6. Keep `POST /api/model/train` and Telegram `/train_model` blocked in live mode; live rollout does not relax training restrictions.
7. Treat `halt_state=runtime_kill_switch` or `halt_state=legacy_circuit_breaker` as a hard hold on new entries until the alert clears.
8. Treat a missing, expired, or revoked `live_rollout_approval.current_approval` record as a startup blocker for `live_shadow`, `live_runtime_entries`, and `live_runtime_primary`.

### Operational Alert Recovery Checklist

If the runtime or model enters a blocked-entry state, inspect these surfaces before re-enabling entries:

1. `GET /api/status` for `operational_alerts`, `halt_state`, `entries_blocked_reason`, `runtime_is_healthy`, and `rollout_health`.
2. `GET /api/model/status` and `GET /api/model/train/status` for model freshness and any active training job.
3. The Telegram `/status` and `/risk` summaries to confirm the same blocked-entry reason is visible to operators.
4. The bot logs for repeated telemetry drops, reconnect storms, startup timeouts, or model reload failures.
5. The same pinned deploy flow used for promotion or rollback if the reviewed profile or effective mode needs to change.

Do not re-enable entries until the alert list is empty, the halt state returns to `none`, and the reviewed profile/mode pair matches the intended rollout target.

### Phase 22 Native Hot-Path Canary Runbook

This runbook is shadow-only. Native ownership is not promoted from it.

1. Enable native hot-path ports in the reviewed build.
2. Feed a native market-data snapshot into `RuntimeApp.native_shadow_snapshot` through the reviewed bridge.
3. Confirm `/api/status.native_shadow_report.native_shadow_status` transitions through:
   - `awaiting_native_snapshot`
   - `match`
   - `stale_native_snapshot` if the snapshot ages out
4. Treat `divergent` as a promotion blocker.
5. Keep native execution read-only until the report stays `match` during the canary soak window.

#### Phase 14 Native Hot-Path Boundary

Phase 14 does not deploy a real native process yet. It defines and validates the boundary that a later native service will use.

The intended first native migration targets are:

- market-data normalization
- execution submission and order-state updates

Before any cutover, the Python runtime should continue to run the same trading logic while a native shadow path emits read-only comparison data through `runtime.native_shadow.NativeShadowBridge`. The operator-facing report appears as `native_shadow_report` in `/api/status` and `/ws/status`.

Recommended validation order:

1. Boot with the native ports wired through `NativeHotPathPorts`.
2. Verify `native_shadow_report.native_shadow_status=awaiting_native_snapshot` while the native path is enabled but has no snapshot yet.
3. Feed or synthesize a native snapshot and confirm `native_shadow_report.native_shadow_status=match`.
4. Treat any `native_shadow_status=divergent` result as a rollout blocker until the mismatch is understood.
5. Keep live trading behavior unchanged until a later phase introduces a real native transport and cutover policy.

#### Phase 15 HFT Operating Modes

The repository should describe three different operating regimes and keep operator expectations aligned with the actual current state:

- **Standard algo**: the legacy candle-cycle bot and its supporting services. Expect slower but simpler behavior, especially around trade scanning and exit management.
- **Low-latency runtime**: the event-driven runtime with runtime projections, rollout profiles, and native shadow comparison. This is the current transition path, not HFT.
- **HFT**: a future venue-near, latency-budgeted, native hot-path regime. Do not use this label for the current production setup.

Practical operator expectations:

- Standard algo rollouts should be judged by cycle success, reconciliation, and conventional trade correctness.
- Low-latency runtime rollouts should be judged by rollout health, queue lag, shadow divergence, and restart safety.
- HFT rollouts will eventually need latency SLOs, kill-switch documentation, and venue-near infrastructure; those are future requirements, not the current baseline.

If you are promoting or rolling back today, keep using the existing paper/live profile flow. The HFT mode described in Phase 15 is a documentation boundary and future operating model, not a new live deploy target yet.

#### Phase 15 Latency SLOs and Degraded-Latency Actions

The observability layer already exposes the fields needed for latency SLOs in `/api/status` and `/ws/status` through `runtime_telemetry`:

- `latency.event_age_ms`
- `latency.exchange_age_ms`
- `latency.queue_lag_ms.<queue>`
- `metric_counts.dropped_event*`

These are operating targets for the low-latency runtime and future HFT mode:

| Signal | Target | Action if exceeded |
|---|---|---|
| `latency.event_age_ms` | Prefer sub-50 ms average | Warn once; hold promotion if it persists |
| `latency.exchange_age_ms` | Prefer sub-25 ms average | Treat as a feed/timestamp issue; block promotion if persistent |
| `latency.queue_lag_ms.<queue>` | Prefer sub-25 ms average | Treat as hot-path backlog; block promotion if repeated |
| `metric_counts.dropped_event*` | Prefer zero growth | Investigate immediately; block or roll back if rising |

Practical operator expectations:

1. The current standard algo mode may tolerate slower cycle timing, but not rising drops or repeated stale-feed signals.
2. The low-latency runtime should be judged on queue health and feed freshness, not candle-cycle wall time.
3. HFT should only be considered when these budgets are consistently met alongside venue-near infrastructure and a native hot path.

Do not read these budgets as a claim that the current production deployment already satisfies HFT SLOs. They are the bar the platform should move toward as the native path becomes real.

#### Phase 21 Microstructure Telemetry in Deployment

When you promote a profile with `runtime.microstructure.enabled=true`, monitor these surfaces during the soak window:

- **Health**: Check that `microstructure.evaluations` is non-zero and growing (depth updates are flowing) and `subscribe_failures` is empty (all streams subscribed).
- **Latency**: Expect `microstructure.decision_latency.avg_ms` around 0.5–1.0 ms (Python in-process). `pipeline_latency.avg_ms` around 1–2 ms (websocket receive to decision). If either doubles, investigate network jitter or CPU load.
- **Signal output**: `microstructure.decision_counts` shows the raw decision distribution (buy/sell ratio). This is **not** trade volume — it's paper decisions only. Use it to tune the strategy thresholds (`min_abs_imbalance`, `min_confidence`) based on a day of live data: if 99% are buys, your thresholds are too loose.
- **Last decision**: The most recent `last_decision` shows symbol, confidence, execution style (passive/aggressive), decision price, and reason (e.g., `bullish_imbalance`). Use it to validate that the strategy is reacting to the market you expect.

Microstructure telemetry failure modes:
- No evaluations after 60s startup: Websocket not connected or gateway not running; check logs for depth/trade subscription errors.
- Decision latency > 5 ms consistently: CPU contention or Python GC pause; watch `event_age_ms` to separate in-process cost from network lag.
- `subscribe_failures` for one symbol: Non-fatal; the stream may retry later or you can redeploy.
- Decision counts all one action (99% buy/sell): Thresholds too loose; revisit `runtime.microstructure.strategy` config after 24 hours of data.

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
| `BINANCE_API_KEY` | Binance API key | - |
| `BINANCE_SECRET_KEY` | Binance API secret | - |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | - |
| `TELEGRAM_CHAT_ID` | Telegram chat ID | - |
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

### 1. `ci.yml` - Continuous Integration

**Triggers:** Push to `master`, `develop`, `feature/**`; PRs to `master`

```
Test -> Smoke Test
```

| Job | Steps |
|---|---|
| **test** | Install deps -> pytest with coverage -> Upload to Codecov |
| **smoke** | Install deps -> Verify app starts -> Check routes registered |

### 2. `deploy.yml` - Continuous Deployment

**Triggers:** `workflow_run` after `CI` succeeds on `master`

```
Checkout exact CI-passed SHA -> Rsync app files -> Render runtime `.env`
-> Install deps only when `requirements.txt` changes -> Install/version the systemd unit
-> Restart service -> Wait for `/api/health` -> Notify Telegram
```

### 3. `train.yml` - Model Training

Handles FreqAI model re-training on schedule or manual trigger.

### Server-Side Training Fallback

When GitHub Actions is unavailable or rate-limited, the server can run the same training flow locally.

Guardrails:

- paper mode only
- trading must be paused first
- only one queued/running training job at a time
- `POST /api/model/train` requires `X-Operator-Token: <AKIBA_OPERATOR_TOKEN>`
- training runs into `models_staging/<job_id>/`
- only validated `*_freqai.joblib` files are promoted into `models/`
- models reload in-process; the service does not restart for a successful training refresh

Recommended operator flow:

1. Pause entries:
   - `POST /api/trading/pause`
   - or Telegram `/stop`
2. Queue training:
   - `POST /api/model/train`
   - or Telegram `/train_model`
3. Watch progress:
   - `GET /api/model/train/status`
   - `GET /api/model/status`
   - or Telegram `/train_status` and `/model`
4. Resume entries only after the job succeeds and `reload_status=reloaded`:
   - `POST /api/trading/resume`
   - or Telegram `/resume`

Example curl:

```bash
curl -X POST http://<host>:8000/api/trading/pause

curl -X POST http://<host>:8000/api/model/train \
  -H "Content-Type: application/json" \
  -H "X-Operator-Token: $AKIBA_OPERATOR_TOKEN" \
  -d '{"pairs":["BTC/USDT","ETH/USDT"],"candles":1000,"timeframe":"1h"}'

curl http://<host>:8000/api/model/train/status
curl http://<host>:8000/api/model/status
```

The pinned snapshot deploy helper now preserves the server-side `models/` directory when the deployed commit does not track model artifacts. That prevents a normal code deploy from silently disabling FreqAI after a successful local training refresh.

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

The standard API entry point shuts down the server if the background trading
loop exits unexpectedly, allowing systemd's `Restart=always` (or Docker's
restart policy) to recover it. `/api/health` returns HTTP 503 for an exited
trading task; Docker probes this endpoint instead of reporting unconditional
success. This is a liveness check, not proof of fresh feeds or generated trades.

If testnet startup fails with DNS errors, compare the server's resolver with
`dig @1.1.1.1 testnet.binance.vision` and `dig @8.8.8.8 testnet.binance.vision`.
Repair the server's persistent DNS configuration if its resolver fails while
those succeed. Keep exchange endpoints and sandbox mode unchanged.

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
