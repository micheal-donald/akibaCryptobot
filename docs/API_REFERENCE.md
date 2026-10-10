# REST & WebSocket API Reference

> Production is moving to [Freqtrade paper trading](FREQTRADE.md). That guide
> defines the replacement engine, configuration, API, risk rules and deployment.
> The custom-engine reference below is retained for historical development.

AkibaCryptoBot exposes a FastAPI server alongside the trading engine. All REST endpoints are prefixed with `/api`. Interactive docs are available at `/docs` (Swagger UI) when the server is running.

**Base URL:** `http://<host>:<port>` (default `http://0.0.0.0:8000`)

---

## Status & Health

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Liveness probe — returns `{"status": "ok"}` while the trading task is active; HTTP 503 if it has exited |
| `GET` | `/api/status` | Bot status: uptime, active trades, runtime mode, rollout health, native-shadow comparison, runtime recovery summary, operational alerts, halt state, circuit breaker state, training job state, strategy, trading mode |
| `GET` | `/api/reconcile` | Manually triggers a reconciliation check between the authoritative trade source and the exchange |

**Example response** (`GET /api/status`):
```json
{
  "is_running": true,
  "trading_paused": false,
  "uptime": "2h 15m",
  "active_trades": 2,
  "circuit_breaker_active": false,
  "circuit_breaker_reason": null,
  "strategy": "ensemble",
  "trading_mode": "paper",
  "configured_deployment_profile": "paper_runtime_primary",
  "effective_runtime_mode": "runtime_primary",
  "effective_runtime_enabled": true,
  "effective_runtime_owns_entries": true,
  "effective_runtime_owns_exits": true,
  "runtime_mode": "runtime_primary",
  "runtime_is_running": true,
  "runtime_start_status": "running",
  "runtime_start_pending": false,
  "runtime_owns_entries": true,
  "runtime_owns_exits": true,
  "runtime_telemetry": {
    "total_events": 128,
    "event_counts": {
      "market.kline": 64
    },
    "metric_counts": {
      "queue_depth.runtime.event_bus": 5
    },
    "queue_depths": {
      "runtime.event_bus": 5
    },
    "last_seen_at": "2024-01-15T10:29:59+00:00",
    "latency": {
      "event_age_ms": {
        "count": 64,
        "total_ms": 1280.0,
        "average_ms": 20.0,
        "min_ms": 8.0,
        "max_ms": 55.0,
        "last_ms": 18.0
      },
      "exchange_age_ms": {
        "count": 64,
        "total_ms": 320.0,
        "average_ms": 5.0,
        "min_ms": 2.0,
        "max_ms": 10.0,
        "last_ms": 4.0
      },
      "queue_lag_ms": {
        "runtime.event_bus": {
          "count": 64,
          "total_ms": 256.0,
          "average_ms": 4.0,
          "min_ms": 1.0,
          "max_ms": 12.0,
          "last_ms": 3.0
        }
      }
    }
  },
  "native_shadow_report": {
    "native_shadow_enabled": true,
    "native_shadow_status": "divergent",
    "native_shadow_is_match": false,
    "native_shadow_mismatch_count": 2,
    "native_shadow_mismatches": [
      "active_position_count",
      "open_order_count"
    ],
    "native_shadow_summary": "Native shadow diverges from Python runtime."
  },
  "runtime_recovery": {
    "processed_trades": 2,
    "restored_symbols": ["BTC/USDT", "ETH/USDT"],
    "protected_symbols": ["BTC/USDT"],
    "orphaned_symbols": [],
    "unprotected_symbols": ["ETH/USDT"],
    "reconcile_error_symbols": [],
    "skipped_symbols": [],
    "issues": [
      {
        "symbol": "ETH/USDT",
        "kind": "unprotected_position",
        "reason": "native protection state unknown after restart; automatic reapply disabled"
      }
    ],
    "issue_count": 1
  },
  "training_job": {
    "current_job": null,
    "last_job": {
      "job_id": "job-12",
      "status": "succeeded",
      "requested_by": "telegram",
      "requested_at": "2024-01-15T09:30:00+00:00",
      "started_at": "2024-01-15T09:30:01+00:00",
      "finished_at": "2024-01-15T09:36:18+00:00",
      "pairs": ["BTC/USDT", "ETH/USDT"],
      "candles": 1000,
      "timeframe": "1h",
      "progress": 100.0,
      "result_summary": "Training finished successfully (1 model(s) reloaded).",
      "error": null,
      "promoted_model_paths": ["models/BTC_USDT_freqai.joblib", "models/ETH_USDT_freqai.joblib"],
      "reload_status": "reloaded"
    },
    "can_start_training": true,
    "active_job_count": 0
  },
  "runtime_is_healthy": true,
  "rollout_environment": "paper",
  "rollout_stage": "primary",
  "state_source": "runtime_projection",
  "rollout_health": "promotion_ready",
  "rollout_recommended_action": "stay_runtime_primary",
  "rollout_summary": "Runtime primary ownership is healthy and ready for operator review.",
  "projected_active_positions": 2,
  "projected_open_orders": 1,
  "projected_pending_exits": 1,
  "projected_rejected_orders": 0
}
```

**Example response** (`GET /api/reconcile`):
```json
{
  "source": "runtime_projection",
  "orphans": [],
  "inventory_only": [
    {
      "asset": "BTC",
      "amount": 0.99266,
      "reason": "Sandbox exchange inventory exists outside the runtime-owned state source"
    }
  ],
  "inventory_only_count": 452,
  "inventory_only_overflow_count": 427,
  "lost_trades": [],
  "mismatches": [],
  "runtime_drift": [],
  "runtime_recovery": {
    "processed_trades": 0,
    "restored_symbols": [],
    "protected_symbols": [],
    "orphaned_symbols": [],
    "unprotected_symbols": [],
    "reconcile_error_symbols": [],
    "skipped_symbols": [],
    "issues": [],
    "issue_count": 0
  }
}
```

`/api/reconcile` now reports the authoritative `source` explicitly. In runtime-owned modes that source is `runtime_projection`. In sandbox/runtime mode, seeded exchange inventory is grouped under `inventory_only` instead of being mixed with actionable runtime drift.

### Live Rollout Approval Fields

When a reviewed live profile is prepared, `/api/status` and `/ws/status` also expose approval and audit metadata:

- `live_rollout_approval.approval_required`
- `live_rollout_approval.target_profile`
- `live_rollout_approval.current_approval`
- `live_rollout_approval.latest_approval`
- `latest_live_rollout_event`

`current_approval` is the active approval record that permits startup for a reviewed live runtime profile. `latest_live_rollout_event` captures the last approval lifecycle event, such as `approval_granted`, `startup_validated`, `startup_rejected`, or `rollback_recorded`.

### Native Shadow Canary States

When `native_hot_path_ports` are enabled, `/api/status` and `/ws/status` may return a richer `native_shadow_report`:

- `native_shadow_status`: `awaiting_native_snapshot`, `stale_native_snapshot`, `match`, `divergent`
- `native_shadow_is_match`: boolean
- `native_shadow_mismatch_count` and `native_shadow_mismatches`
- `native_shadow_last_seen_at`
- `native_shadow_age_seconds`
- `native_shadow_recommended_action`
- `native_shadow_stale_after_seconds`

`awaiting_native_snapshot` means the native bridge is enabled but has not emitted a usable snapshot yet. `stale_native_snapshot` means the last snapshot is older than the canary freshness budget. `divergent` means the native snapshot and Python runtime disagree and promotion should be blocked.
---

## Account

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/balance` | Live wallet balances from Binance |
| `GET` | `/api/config` | Current bot configuration (pairs, risk params, strategy) |

---

## Trading Control

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/trades` | All currently open positions with entry prices |
| `POST` | `/api/trading/pause` | Pause new entries while leaving the bot lifecycle, exits, and API online |
| `POST` | `/api/trading/resume` | Resume new entries |
| `GET` | `/api/trading/live/approval` | Current live rollout approval requirement, active approval, and latest audit event |
| `POST` | `/api/trading/live/approve` | Grant a time-bounded approval for a reviewed live runtime profile. Requires `X-Operator-Token` |
| `POST` | `/api/trading/live/rollback` | Revoke active live rollout approvals and record a rollback audit event. Requires `X-Operator-Token` |

**Example response** (`GET /api/trades`):
```json
{
  "count": 1,
  "trades": [
    {
      "position_id": "pos-1",
      "symbol": "BTC/USDT",
      "side": "buy",
      "amount": 0.001,
      "entry_price": 42500.0,
      "stop_loss": 41650.0,
      "take_profit": 44200.0,
      "trailing_stop_price": null,
      "status": "open",
      "source": "runtime_projection"
    }
  ],
  "source": "runtime_projection",
  "runtime_mode": "runtime_primary"
}
```

**Example response** (`GET /api/trading/live/approval`):
```json
{
  "target_profile": "live_shadow",
  "approval_required": true,
  "current_approval": {
    "target_profile": "live_shadow",
    "approved_by": "ops",
    "reason": "Approved canary rollout.",
    "approved_at": "2026-04-09T08:00:00+00:00",
    "expires_at": "2026-04-10T08:00:00+00:00",
    "status": "active"
  },
  "latest_approval": {
    "target_profile": "live_shadow",
    "approved_by": "ops",
    "reason": "Approved canary rollout.",
    "approved_at": "2026-04-09T08:00:00+00:00",
    "expires_at": "2026-04-10T08:00:00+00:00",
    "status": "active"
  },
  "latest_event": {
    "event_type": "startup_validated",
    "target_profile": "live_shadow",
    "actor": "system",
    "reason": "Startup approval validated.",
    "created_at": "2026-04-09T08:01:00+00:00"
  }
}
```

**Request body** (`POST /api/trading/live/approve`):
```json
{
  "target_profile": "live_shadow",
  "actor": "ops",
  "reason": "Approved canary rollout",
  "expires_in_hours": 24
}
```

**Request body** (`POST /api/trading/live/rollback`):
```json
{
  "actor": "ops",
  "reason": "Canary degraded after warm-up",
  "target_profile": "live_shadow"
}
```

---

## Performance Metrics

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/metrics` | Comprehensive metrics: Sharpe, Sortino, profit factor, win rate, drawdown |
| `GET` | `/api/equity-curve?range=7d` | Time-series equity data for charting (Lightweight Charts format) |
| `GET` | `/api/trades/history?limit=20&offset=0` | Completed trade log, newest first, paginated |
| `GET` | `/api/strategy/performance` | Per-strategy performance breakdown |
| `GET` | `/api/performance/low-latency` | Low-latency replay/shadow performance summary with decision latency, submit latency, fill quality, and capture health |

**Query parameters** (`GET /api/equity-curve`):

| Parameter | Type | Default | Description |
|---|---|---|---|
| `range` | string | `all` | Time window: `1d`, `7d`, `30d`, `90d`, or `all`. The last point before the window is included as an anchor so the line spans the full range. |

Points are sorted, strictly increasing per second, and LTTB-downsampled to at most 500 points regardless of history length. `time` is epoch seconds (UTC) — the format TradingView Lightweight Charts consumes directly.

**Example response** (`GET /api/equity-curve?range=7d`):
```json
{
  "range": "7d",
  "initial_balance": 10000.0,
  "data": [
    { "time": 1752192000, "value": 10000.0 },
    { "time": 1752195600, "value": 10142.5 }
  ]
}
```

**Example response** (`GET /api/performance/low-latency`):
```json
{
  "available": true,
  "source": "depth_capture_replay",
  "generated_at": "2024-01-15T10:30:00+00:00",
  "capture_health": {
    "total_records": 2,
    "depth_records": 1,
    "trade_records": 1,
    "snapshot_records": 1,
    "stale_snapshots": 0,
    "gap_snapshots": 0,
    "sequence_issues": 0
  },
  "feature_rows": 1,
  "decision_count": 1,
  "fill_count": 1,
  "buy_count": 1,
  "sell_count": 0,
  "average_decision_latency_ms": 12.5,
  "average_submit_latency_ms": 4.0,
  "average_fill_quality_bps": 24.9,
  "average_slippage_bps": 0.0,
  "average_spread_capture_bps": 24.9
}
```

**Query parameters** (`/api/trades/history`):

| Param | Type | Default | Range | Description |
|---|---|---|---|---|
| `limit` | int | 20 | 1–100 | Page size |
| `offset` | int | 0 | ≥ 0 | Number of trades to skip (newest first). Use with `total` / `has_more` in the response to page through history. |

Response envelope:

```json
{
  "count": 15,
  "total": 175,
  "offset": 0,
  "limit": 15,
  "has_more": true,
  "trades": [ ... ]
}
```

When `bot.trade_store` is available the route paginates against the SQLite-backed trade log (deep history). Otherwise it falls back to the in-memory performance tracker cache (last 100 trades).

---

## Market Data

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/ohlcv?symbol=BTC/USDT&timeframe=15m&limit=300` | Candlestick data for the dashboard price-action chart |

**Query parameters** (`GET /api/ohlcv`):

| Parameter | Type | Default | Description |
|---|---|---|---|
| `symbol` | string | required | Must be one of the configured trading pairs (prevents open-proxy abuse) |
| `timeframe` | string | `15m` | One of `1m`, `5m`, `15m`, `1h`, `4h`, `1d` |
| `limit` | int | `300` | Number of candles, 10–1000 |

`time` is epoch seconds (UTC), directly consumable by TradingView Lightweight Charts.

**Example response**:
```json
{
  "symbol": "BTC/USDT",
  "timeframe": "15m",
  "data": [
    { "time": 1752570000, "open": 60000.0, "high": 60120.5, "low": 59950.0, "close": 60080.2, "volume": 12.4 }
  ]
}
```

---

## Risk Management

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/risk` | Risk report: Kelly criterion, ATR sizing, daily PnL, drawdown |
| `GET` | `/api/correlations` | Asset correlation matrix for tracked pairs |
| `GET` | `/api/heatmap` | Portfolio exposure heatmap across correlated assets |
| `GET` | `/api/circuit-breaker` | Circuit breaker status (active/inactive + reason) |

---

## Strategy

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/strategy` | Active strategy name, available strategies, component status |
| `POST` | `/api/strategy/switch` | Hot-swap the active strategy at runtime |

**Request body** (`POST /api/strategy/switch`):
```json
{
  "strategy_name": "momentum"
}
```

**Response:**
```json
{
  "status": "switched",
  "active_strategy": "momentum",
  "available_strategies": ["momentum", "ensemble", "freqai_ml_freqai"]
}
```

---

## Model Health

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/model/status` | FreqAI model info: age, staleness, feature count, top features, last successful training, reload state |
| `GET` | `/api/model/train/status` | Current and last server-side training job state |
| `POST` | `/api/model/train` | Queue a server-side FreqAI training job. Requires `X-Operator-Token`, paper mode, and paused trading |

**Example response:**
```json
{
  "freqai_active": true,
  "last_success_at": "2024-01-15T09:36:18+00:00",
  "reload_status": "reloaded",
  "current_job_id": null,
  "models": [
    {
      "name": "MLFreqAIStrategy",
      "type": "MLFreqAIStrategy",
      "weight": 0.6,
      "trained": true,
      "feature_count": 30,
      "last_trained": "2024-01-15T08:00:00",
      "age_hours": 2.5,
      "stale": false,
      "top_features": [
        {"name": "rsi_14", "importance": 0.0821},
        {"name": "macd_histogram", "importance": 0.0654}
      ]
    }
  ]
}
```

**Example response** (`GET /api/model/train/status`):
```json
{
  "current_job": {
    "job_id": "job-13",
    "status": "running",
    "requested_by": "api",
    "requested_at": "2024-01-15T10:00:00+00:00",
    "started_at": "2024-01-15T10:00:01+00:00",
    "finished_at": null,
    "pairs": ["BTC/USDT"],
    "candles": 1200,
    "timeframe": "1h",
    "progress": 42.0,
    "result_summary": "Training subprocess is running.",
    "error": null,
    "promoted_model_paths": [],
    "reload_status": "training"
  },
  "last_job": {
    "job_id": "job-12",
    "status": "succeeded",
    "requested_by": "telegram",
    "requested_at": "2024-01-15T09:30:00+00:00",
    "started_at": "2024-01-15T09:30:01+00:00",
    "finished_at": "2024-01-15T09:36:18+00:00",
    "pairs": ["BTC/USDT", "ETH/USDT"],
    "candles": 1000,
    "timeframe": "1h",
    "progress": 100.0,
    "result_summary": "Training finished successfully (1 model(s) reloaded).",
    "error": null,
    "promoted_model_paths": ["models/BTC_USDT_freqai.joblib", "models/ETH_USDT_freqai.joblib"],
    "reload_status": "reloaded"
  },
  "can_start_training": false,
  "active_job_count": 1,
  "start_blocker": "training_job_already_active"
}
```

**Request body** (`POST /api/model/train`):
```json
{
  "pairs": ["BTC/USDT", "ETH/USDT"],
  "candles": 1000,
  "timeframe": "1h"
}
```

**Headers**

| Header | Required | Meaning |
|---|---|---|
| `X-Operator-Token` | yes | Shared secret from `AKIBA_OPERATOR_TOKEN` |

**Rejection reasons**

| HTTP | `detail` |
|---|---|
| `403` | `operator_token_invalid` |
| `503` | `operator_token_not_configured` |
| `409` | `training_not_allowed_in_live` |
| `409` | `trading_must_be_paused` |
| `409` | `training_job_already_active` |
| `409` | `freqai_not_available` |

---

## Runtime Telemetry

`runtime_telemetry` appears inside `/api/status` and `/ws/status` when the runtime exposes operator metrics. It is the main operator-facing snapshot for Phase 12.

### Top-Level Fields

| Field | Meaning |
|---|---|
| `total_events` | Total runtime events observed since startup |
| `event_counts` | Count by runtime event type, such as `market.kline` or `signal.suppressed` |
| `metric_counts` | Low-level counters emitted by the runtime services |
| `queue_depths` | Current queue depth by queue name |
| `last_seen_at` | Timestamp of the most recently observed runtime event |
| `latency` | Nested latency summaries for event age, exchange age, and queue lag |
| `signals` | Recent strategy decisions and counts, including suppressed or non-actionable signals |
| `user_stream` | Authenticated Binance user stream connectivity and reconnect/event counters |

When `user_stream.connected` is explicitly false, the runtime reports unhealthy
and blocks new entries. This prevents trading without timely order and fill
updates; the stream reconnects automatically.

### Latency Metrics

| Field | Meaning |
|---|---|
| `latency.event_age_ms` | Time from `ts_received` to runtime observation time. If this rises steadily, the runtime is backing up behind ingestion. |
| `latency.exchange_age_ms` | Time from `ts_exchange` to `ts_received`. If this rises steadily, the feed is stale or timestamping is skewed. Negative samples are ignored. |
| `latency.queue_lag_ms.<queue>` | Time an event waited before a hot-path queue drained it. Rising queue lag with rising queue depth is backlog, not noise. |

Each latency summary includes:

- `count`
- `total_ms`
- `average_ms`
- `min_ms`
- `max_ms`
- `last_ms`

### Operating Targets

The telemetry fields are useful for judging whether the platform is only live or actually healthy enough for a low-latency rollout.

| Field | Target | Interpretation |
|---|---|---|
| `latency.event_age_ms` | Prefer sub-50 ms average | The runtime is keeping up with its own ingest path |
| `latency.exchange_age_ms` | Prefer sub-25 ms average | The feed is fresh and timestamping is sane |
| `latency.queue_lag_ms.<queue>` | Prefer sub-25 ms average | The hot path is not backing up on that queue |
| `metric_counts.dropped_event*` | Prefer no sustained increase | Runtime work is not being dropped under load |

These are targets for the low-latency runtime and future HFT regime. They are not proof that the current deployment already meets HFT-class latency SLOs.

### Metric Counters

The runtime emits counters with stable names so operators can tell backlog from data-quality problems:

- `event_age_sample.<event_type>`
- `exchange_age_sample.<event_type>`
- `queue_depth.<queue_name>`
- `queue_lag_sample.<queue_name>`
- `dropped_event`
- `dropped_event.<consumer>`
- `dropped_event.<consumer>.<event_type>`
- `reconnect.<source>`
- `stale_symbol`
- `stale_symbol.<symbol>`
- `shadow_drift`
- `shadow_drift.<symbol>`
- `missing_symbol`
- `missing_symbol.<symbol>`
- `missing_symbol.<side>`

### Operator Interpretation

- Treat persistent growth in `queue_depths` plus `queue_lag_ms` as a promotion blocker.
- Treat sustained `dropped_event*` counts as a promotion blocker.
- Treat rising `exchange_age_ms` as a feed problem until the exchange or timestamp path is proved healthy.
- Treat `runtime_start_pending=true` as startup warm-up, not as a rollback signal.
- In `paper_runtime_primary`, the healthy target is `rollout_health=promotion_ready` with `rollout_recommended_action=stay_runtime_primary`.
- In `paper_runtime_entries`, the healthy target is `rollout_health=promotion_ready` with `rollout_recommended_action=promote_runtime_primary`.
- In `paper_shadow`, the healthy target is `rollout_health=shadow_ready` with `rollout_recommended_action=continue_shadow`.

---

## Operational Alerts & Halt State

When the runtime, model, or training control plane detects sustained degradation, `/api/status` and `/ws/status` include a canonical operational alert summary.

### Status Fields

| Field | Meaning |
|---|---|
| `operational_alerts` | Active alerts with code, severity, source, message, and recommended action |
| `halt_state` | Current entry-blocking state: `none`, `entries_blocked`, `runtime_kill_switch`, or `legacy_circuit_breaker` |
| `entries_blocked_reason` | Alert code that caused the current entry block, if any |
| `active_critical_alert_count` | Number of currently active critical alerts |

### Operator Semantics

- `halt_state=none` means new entries are not blocked by operational alerts.
- Any non-`none` halt state means do not re-enable entries until the alert set is empty and the reviewed recovery path is satisfied.
- In runtime-owned modes, the alert path blocks new entries only; exits continue to run.
- In legacy-owned modes, the same alert path uses the legacy entry-pause seam rather than forcing a full stop.
- The recommended operator action is always shown in the alert records and should be preferred over ad hoc manual recovery.

The same fields are mirrored through `/ws/status` and the Telegram `/status` and `/risk` summaries so the API, websocket stream, and chat notifier stay aligned.

---

## Phase 20 Live Rollout Gates

The live runtime ladder is now documented as an operator workflow rather than just a set of reviewed profiles.

### Stage Expectations

| Stage | Expected status fields | Expected action |
|---|---|---|
| `live_shadow` | `rollout_environment=live`, `rollout_stage=canary`, `rollout_health=shadow_ready` during steady state, `runtime_is_healthy=true`, `halt_state=none` | `promote_live_runtime_entries` |
| `live_runtime_entries` | `rollout_environment=live`, `rollout_stage=canary`, `rollout_health=promotion_ready`, `runtime_owns_entries=true`, `runtime_owns_exits=false`, `halt_state=none` | `promote_live_runtime_primary` |
| `live_runtime_primary` | `rollout_environment=live`, `rollout_stage=primary`, `rollout_health=promotion_ready`, `runtime_owns_entries=true`, `runtime_owns_exits=true`, `halt_state=none` | `stay_live_runtime_primary` |
| `live_legacy` rollback | `rollout_environment=live`, `rollout_stage=legacy`, `rollout_health=legacy`, `runtime_is_healthy=true`, `halt_state=none` | `stay_live_legacy` |
| Live canary with halt | `halt_state!=none`, `rollout_health=promotion_blocked`, runtime still healthy enough to report status | `clear_halt_before_promotion` |
| Live primary with halt | `halt_state!=none`, `rollout_health=promotion_blocked`, `runtime_owns_exits=true` | `clear_halt_before_continuing_live_primary` |

### Live Training Restriction

Live canary profiles keep training locked down:

- `POST /api/model/train` must reject `live_shadow`, `live_runtime_entries`, and `live_runtime_primary` with `training_not_allowed_in_live`.
- `GET /api/model/train/status` should show no queued/running job before a live promotion is attempted.
- `training_job.can_start_training` should remain `false` in live rollout status surfaces.
- Telegram `/train_model` should mirror the API refusal and explain that training is paper-only.

### Live Alert Gating

If `/api/status` reports `halt_state` other than `none`, live promotion is blocked and the rollout summary changes to `rollout_health=promotion_blocked`. A live canary should report `clear_halt_before_promotion`; a halted `live_runtime_primary` stage should report `clear_halt_before_continuing_live_primary`. A live stage that reports `rollback_recommended` should be rolled back to `live_legacy` after the warm-up window, not promoted further.

---

## WebSocket Streams

| Endpoint | Push Interval | Description |
|---|---|---|
| `ws://<host>:<port>/ws/prices` | 1 second | Real-time price ticks from the Binance exchange cache |
| `ws://<host>:<port>/ws/status` | 5 seconds | Bot status updates with deployment profile, live approval/audit state, effective runtime mode, ownership, operational alerts, training-job state, and projection metadata |

**Message format** (`/ws/prices`):
```json
{
  "type": "prices",
  "timestamp": "2024-01-15T10:30:00",
  "data": {
    "BTC/USDT": 42500.5,
    "ETH/USDT": 2250.3
  }
}
```

**Message format** (`/ws/status`):
```json
{
  "type": "status",
  "data": {
    "is_running": true,
    "active_trades": 2,
    "circuit_breaker_active": false,
    "strategy": "ensemble",
    "trading_mode": "paper",
    "configured_deployment_profile": "paper_runtime_primary",
    "effective_runtime_mode": "runtime_primary",
    "effective_runtime_enabled": true,
    "effective_runtime_owns_entries": true,
    "effective_runtime_owns_exits": true,
    "runtime_mode": "runtime_primary",
    "runtime_is_running": true,
    "runtime_start_status": "running",
    "runtime_start_pending": false,
    "runtime_owns_entries": true,
    "runtime_owns_exits": true,
    "runtime_telemetry": {
      "total_events": 128,
      "event_counts": {"market.kline": 64},
      "metric_counts": {"queue_depth.runtime.event_bus": 5},
      "queue_depths": {"runtime.event_bus": 5},
      "last_seen_at": "2024-01-15T10:29:59+00:00",
      "latency": {
        "event_age_ms": {"count": 64, "total_ms": 1280.0, "average_ms": 20.0, "min_ms": 8.0, "max_ms": 55.0, "last_ms": 18.0},
        "exchange_age_ms": {"count": 64, "total_ms": 320.0, "average_ms": 5.0, "min_ms": 2.0, "max_ms": 10.0, "last_ms": 4.0},
        "queue_lag_ms": {"runtime.event_bus": {"count": 64, "total_ms": 256.0, "average_ms": 4.0, "min_ms": 1.0, "max_ms": 12.0, "last_ms": 3.0}}
      }
    },
    "runtime_is_healthy": true,
    "live_rollout_approval": {
      "approval_required": false,
      "target_profile": null,
      "current_approval": null,
      "latest_approval": null
    },
    "latest_live_rollout_event": null,
    "rollout_environment": "paper",
    "rollout_stage": "primary",
    "state_source": "runtime_projection",
    "rollout_health": "promotion_ready",
    "rollout_recommended_action": "stay_runtime_primary",
    "rollout_summary": "Runtime primary ownership is healthy and ready for operator review.",
    "projected_active_positions": 2,
    "projected_open_orders": 1,
    "projected_pending_exits": 1,
    "projected_rejected_orders": 0,
    "timestamp": "2024-01-15T10:30:05"
  }
}
```

During a fresh runtime rollout, `/api/status` and `/ws/status` can briefly report:

- `runtime_is_running=false`
- `runtime_start_status="configured"` or `"starting"`
- `runtime_start_pending=true`
- `rollout_health="startup_pending"`
- `rollout_recommended_action="wait_for_runtime"`

That state means the runtime is still warming up. It is not, by itself, a rollback signal.

When a reviewed live profile is active, the same surfaces expose:

- `rollout_environment="live"`
- `rollout_stage="canary"` for `live_shadow` and `live_runtime_entries`
- `rollout_stage="primary"` for `live_runtime_primary`
- live-specific actions such as `promote_live_runtime_entries`, `promote_live_runtime_primary`, `stay_live_runtime_primary`, and `rollback_to_live_legacy`

---

## Authentication

Production's portfolio dashboard sets `AKIBA_PUBLIC_READ_ONLY=true`. An application
boundary rejects every HTTP write and any API path outside the explicit read
allowlist with HTTP 403 (`public_dashboard_read_only`), even with a valid operator
token. `/api/reconcile` is blocked because it triggers work. Only the outgoing
`/ws/prices` and `/ws/status` streams are permitted. API documentation is disabled;
strategy selection is disabled and pause controls are hidden in the dashboard.
The bot continues autonomous paper trading; Telegram remains the operator channel.

The default development mode retains control endpoints; some require
`X-Operator-Token`, while pause/resume and strategy switching do not. Keep that
mode private. CORS does not provide authentication.

---

## Running the API

```bash
# Default: bot + API on port 8000
python start_bot.py

# Custom port
python start_bot.py --port 8080

# Bot only, no API server
python start_bot.py --no-api
```

API docs: `http://localhost:8000/docs`
