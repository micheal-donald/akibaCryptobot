# REST & WebSocket API Reference

AkibaCryptoBot exposes a FastAPI server alongside the trading engine. All REST endpoints are prefixed with `/api`. Interactive docs are available at `/docs` (Swagger UI) when the server is running.

**Base URL:** `http://<host>:<port>` (default `http://0.0.0.0:8000`)

---

## Status & Health

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Liveness probe — returns `{"status": "ok"}` |
| `GET` | `/api/status` | Bot status: uptime, active trades, runtime mode, rollout health, circuit breaker state, strategy, trading mode |
| `GET` | `/api/reconcile` | Manually triggers a reconciliation check between DB and Exchange |

**Example response** (`GET /api/status`):
```json
{
  "is_running": true,
  "uptime": "2h 15m",
  "active_trades": 2,
  "circuit_breaker_active": false,
  "circuit_breaker_reason": null,
  "strategy": "ensemble",
  "trading_mode": "paper",
  "runtime_mode": "runtime_primary",
  "runtime_is_running": true,
  "runtime_owns_entries": true,
  "runtime_owns_exits": true,
  "runtime_is_healthy": true,
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
| `POST` | `/api/trading/pause` | Pause the trading engine (stops scanning for new trades) |
| `POST` | `/api/trading/resume` | Resume the trading engine |

**Example response** (`GET /api/trades`):
```json
{
  "count": 1,
  "trades": {
    "BTC/USDT": {
      "side": "buy",
      "amount": 0.001,
      "entry_price": 42500.0,
      "stop_loss": 41650.0,
      "take_profit": 44200.0,
      "timestamp": "2024-01-15T10:30:00"
    }
  }
}
```

---

## Performance Metrics

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/metrics` | Comprehensive metrics: Sharpe, Sortino, profit factor, win rate, drawdown |
| `GET` | `/api/equity-curve` | Time-series equity data for charting |
| `GET` | `/api/trades/history?limit=20` | Completed trade log (max 100) |
| `GET` | `/api/strategy/performance` | Per-strategy performance breakdown |

**Query parameters** (`/api/trades/history`):

| Param | Type | Default | Range | Description |
|---|---|---|---|---|
| `limit` | int | 20 | 1–100 | Number of recent trades to return |

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
| `GET` | `/api/model/status` | FreqAI model info: age, staleness, feature count, top features |

**Example response:**
```json
{
  "freqai_active": true,
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

---

## WebSocket Streams

| Endpoint | Push Interval | Description |
|---|---|---|
| `ws://<host>:<port>/ws/prices` | 1 second | Real-time price ticks from the Binance exchange cache |
| `ws://<host>:<port>/ws/status` | 5 seconds | Bot status updates with runtime mode, ownership, and projection metadata |

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
    "runtime_mode": "runtime_primary",
    "runtime_is_running": true,
    "runtime_owns_entries": true,
    "runtime_owns_exits": true,
    "runtime_is_healthy": true,
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

---

## Authentication

> [!WARNING]
> The API currently has **no authentication**. CORS is configured to allow all origins (`*`). This is suitable for local/dev usage only. For production, add API key middleware or restrict origins.

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
