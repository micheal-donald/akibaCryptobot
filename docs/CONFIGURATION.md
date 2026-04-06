# Configuration Reference

AkibaCryptoBot is configured via two JSON files and environment variables.

---

## Environment Variables (`.env`)

| Variable | Description | Default |
|---|---|---|
| `BINANCE_API_KEY` | Binance API key | — |
| `BINANCE_SECRET_KEY` | Binance API secret | — |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | — |
| `TELEGRAM_CHAT_ID` | Telegram chat ID for notifications | — |
| `TRADING_MODE` | `paper` or `live` | `paper` |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` |
| `AKIBA_STRATEGY` | Service strategy override | `ensemble` |
| `AKIBA_HOST` | API bind host for service mode | `0.0.0.0` |
| `AKIBA_PORT` | API port for service mode | `8000` |
| `AKIBA_CONFIG` | Config path for service mode | `config/config.json` |
| `AKIBA_NO_API` | Disable API in service mode | `false` |
| `AKIBA_DRY_RUN` | Run service in dry-run mode | `false` |
| `AKIBA_OPERATOR_TOKEN` | Shared secret required by mutating operator endpoints such as `POST /api/model/train` | — |
| `MAX_POSITION_SIZE` | Max portfolio fraction per trade | `0.05` |
| `MAX_DAILY_LOSS` | Max daily loss fraction | `0.02` |
| `STOP_LOSS_PERCENT` | Default stop-loss percentage | `0.02` |
| `TAKE_PROFIT_PERCENT` | Default take-profit percentage | `0.04` |

> [!TIP]
> Copy `.env.example` to `.env` and fill in your runtime credentials. Keep deployment-only values such as `SERVER_IP` and SSH/Vultr secrets in GitHub Actions secrets and variables, not in the local `.env`.

For paper rollout work, prefer the reviewed deployment profile helper before you edit `config/config.json` by hand. It keeps the profile and compatibility fields synchronized so the resulting diff is explicit:

```bash
python scripts/set_runtime_profile.py paper_shadow
python scripts/set_runtime_profile.py paper_runtime_entries
python scripts/set_runtime_profile.py paper_runtime_primary
python scripts/set_runtime_profile.py paper_legacy  # rollback
python scripts/set_runtime_profile.py live_shadow
python scripts/set_runtime_profile.py live_runtime_entries
python scripts/set_runtime_profile.py live_runtime_primary
python scripts/set_runtime_profile.py live_legacy  # live rollback
```

After running the helper, review the local diff before you deploy. The helper updates `runtime.deployment_profile`, `runtime.runtime_mode`, and `runtime.shadow_mode` together so the config stays reviewable and unambiguous.

The deployed server should then reflect the same values in `/api/status` through `configured_deployment_profile`, `effective_runtime_mode`, `effective_runtime_enabled`, `effective_runtime_owns_entries`, and `effective_runtime_owns_exits`.

Server-side FreqAI training is guarded separately from deployment profiles:

- it is allowed only when `exchange.sandbox=true`
- trading must be paused first
- `POST /api/model/train` requires `AKIBA_OPERATOR_TOKEN` in `X-Operator-Token`
- training runs in a staging directory and only promotes validated artifacts into `models/`

---

## `config/config.json`

### `exchange`

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | string | `"binance"` | Exchange name (only Binance supported) |
| `sandbox` | bool | `true` | Use testnet when `true` |
| `ccxt_config.enableRateLimit` | bool | `true` | Respect exchange rate limits |
| `ccxt_config.rateLimit` | int | `1200` | Milliseconds between requests |

### `trading`

| Key | Type | Default | Description |
|---|---|---|---|
| `pairs` | list | `["BTC/USDT", "ETH/USDT", "BNB/USDT", "ADA/USDT"]` | Trading pairs to monitor |
| `timeframes.primary` | string | `"1h"` | Primary candle timeframe |
| `timeframes.secondary` | string | `"15m"` | Secondary timeframe (multi-TF analysis) |
| `strategy` | string | `"ensemble"` | Active strategy: `momentum`, `ensemble`, `freqai_ml_freqai` |
| `max_open_trades` | int | `3` | Maximum concurrent positions |
| `stake_amount` | float | `0.05` | Fraction of balance per trade |
| `stake_currency` | string | `"USDT"` | Quote currency for sizing |

### `runtime`

The `runtime` section controls the rollout contract for the event-driven runtime.
It is additive: if the section is omitted, the bot stays on the legacy loop.

| Key | Type | Default | Description |
|---|---|---|---|
| `deployment_profile` | string | reviewed profile in `config/config.json` | Reviewed deployment profile: `paper_legacy`, `paper_shadow`, `paper_runtime_entries`, `paper_runtime_primary`, `live_legacy`, `live_shadow`, `live_runtime_entries`, `live_runtime_primary` |
| `runtime_mode` | string | compatibility-only | Explicit rollout mode. When `deployment_profile` is present, this must match the profile-derived effective mode |
| `shadow_mode` | bool | compatibility-only | Legacy fallback when `runtime_mode` is omitted. When `deployment_profile` is present, this must match the profile-derived shadow semantics if provided |
| `allow_live_runtime` | bool | `false` | Explicit override for live exchange configs. Required for `shadow`, `runtime_entries`, and `runtime_primary` when `exchange.sandbox` is `false`. Does not activate the runtime by itself |
| `symbols` | list | inherits from `trading.pairs` | Runtime-tracked symbols |
| `kline_intervals` | list | inherits from `trading.timeframes` | Runtime candle intervals |
| `market_data_source` | string | `"binance"` | Runtime market-data source |
| `sandbox_execution` | bool | `false` | Runtime execution testnet toggle |
| `sandbox_market_data` | bool | `false` | Runtime market-data testnet toggle |
| `queue_maxsize` | int | `0` | Shared queue bound; `0` keeps the queue unbounded |
| `event_bus_maxsize` | int | `0` | Alias for `queue_maxsize` |
| `candle_history_limit` | int | `2048` | Rolling candle buffer length |
| `stale_after_seconds` | float | `30.0` | Staleness cutoff for market data |
| `housekeeping_interval_seconds` | float | `60.0` | Background housekeeping cadence |

Rollout guidance:

| Mode | Meaning |
|---|---|
| `paper_legacy` | Paper/testnet deployment profile that keeps the classic 60-second bot loop as the source of truth |
| `paper_shadow` | Paper/testnet deployment profile that runs the runtime in read-only/shadow mode for comparison |
| `paper_runtime_entries` | Paper/testnet deployment profile where the runtime owns entries and legacy exit sweeps may still be used for compatibility |
| `paper_runtime_primary` | Paper/testnet deployment profile where the runtime owns both entries and exits |
| `live_legacy` | Live deployment profile that maps to the legacy loop |
| `live_shadow` | Live canary profile where the runtime observes but the legacy loop remains authoritative |
| `live_runtime_entries` | Live canary profile where the runtime owns entries and the legacy loop retains exit authority |
| `live_runtime_primary` | Live primary profile where the runtime owns both entries and exits |
| `legacy` | Keep the classic 60-second bot loop as the source of truth |
| `shadow` | Run the runtime in read-only/shadow mode for comparison |
| `runtime_entries` | Runtime owns entries; legacy exit sweeps may still be used for compatibility |
| `runtime_primary` | Runtime owns both entries and exits |

For the full paper activation sequence, see [docs/DEPLOYMENT.md](/home/lekim/Projects/akibaCryptobot/docs/DEPLOYMENT.md#paper-activation-procedure). That guide shows the staged promotion path and rollback steps the operator should follow when moving from `legacy` to `shadow`, `runtime_entries`, and `runtime_primary`.

> [!NOTE]
> `deployment_profile` is the reviewed deployment surface and is preferred for paper rollout promotion. Use `scripts/set_runtime_profile.py` to update the local sample config so `deployment_profile`, `runtime_mode`, and `shadow_mode` stay synchronized. If `deployment_profile` is set, the config layer derives the effective runtime mode from the profile and rejects contradictory `runtime_mode` or `shadow_mode` values. If `deployment_profile` is omitted, `runtime_mode` remains the explicit rollout mode and `shadow_mode` is the compatibility fallback: `true` resolves to `shadow`, `false` resolves to `runtime_entries`. When `runtime_mode` is set explicitly, it wins and `shadow_mode` is normalized to match it.
>
> Live exchange deployments reject runtime-owned modes by default. `live_legacy` is allowed without any extra flag, but `shadow`, `runtime_entries`, and `runtime_primary` require `runtime.allow_live_runtime=true` when `exchange.sandbox=false`. Setting `allow_live_runtime` alone does not activate the runtime.

### `risk_management`

| Key | Type | Default | Description |
|---|---|---|---|
| `max_position_size` | float | `0.05` | Max position as fraction of portfolio |
| `max_daily_loss` | float | `0.02` | Daily loss limit (2%) |
| `stop_loss_percent` | float | `0.02` | Default stop-loss distance |
| `take_profit_percent` | float | `0.04` | Default take-profit distance |
| `trailing_stop` | bool | `true` | Enable trailing stops |
| `trailing_stop_positive` | float | `0.01` | Trailing stop activation offset |
| `cooldown_candles` | int | `5` | Candles to wait after a trade before re-entering |
| `max_spread_percent` | float | `0.5` | Max acceptable bid-ask spread (%) |

### `strategy_config`

| Key | Type | Default | Description |
|---|---|---|---|
| `min_signal_strength` | float | `0.45` | Minimum signal strength to act (0.0–1.0) |
| `rsi_oversold` | int | `30` | RSI oversold threshold |
| `rsi_overbought` | int | `70` | RSI overbought threshold |
| `stoch_oversold` | int | `20` | Stochastic oversold threshold |
| `stoch_overbought` | int | `80` | Stochastic overbought threshold |
| `adx_threshold` | int | `25` | ADX minimum for trend confirmation |
| `min_volume_ratio` | float | `1.2` | Volume must be this multiple of average |

### `freqai`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Enable FreqAI ML strategies |
| `model` | string | `"LightGBMClassifier"` | ML model type |
| `identifier` | string | `"akiba_freqai_model"` | Model identifier for persistence |
| `train_period` | int | `200` | Number of candles for training |
| `model_expiry_hours` | int | `48` | Re-train if model older than this |
| `include_timeframes` | list | `["1h", "4h"]` | Timeframes for feature engineering |
| `include_corr_pairs` | list | `[]` | Correlated pairs for features |
| `data_split_parameters.test_size` | float | `0.2` | Train/test split ratio |
| `data_split_parameters.random_state` | int | `42` | Random seed for reproducibility |
| `ml_params.lookahead_period` | int | `5` | Candles ahead for target labelling |
| `ml_params.training_data_period` | int | `1000` | Historical candles for training data |
| `ml_params.n_jobs` | int | model default | Parallel workers for offline training. Server-triggered training overrides this to `1` to avoid starving the trading process |

### `persistence`

| Key | Type | Default | Description |
|---|---|---|---|
| `db_path` | string | `"data/trades.db"` | SQLite database path for trade persistence |

### `circuit_breaker`

| Key | Type | Default | Description |
|---|---|---|---|
| `volatility_threshold` | float | `3.0` | Halt trading if volatility exceeds this multiple of normal |
| `drawdown_threshold` | float | `0.20` | Halt trading if portfolio drawdown exceeds 20% |
| `reconciliation_interval_minutes` | int | `60` | Frequency of DB-Exchange audits |

### `notifications`

| Key | Type | Default | Description |
|---|---|---|---|
| `telegram_enabled` | bool | `true` | Enable Telegram notifications |
| `trade_notifications` | bool | `true` | Send alerts on trade entry/exit |
| `error_notifications` | bool | `true` | Send alerts on errors |
| `daily_reports` | bool | `true` | Send daily performance reports |

### `logging`

| Key | Type | Default | Description |
|---|---|---|---|
| `level` | string | `"DEBUG"` | Log level |
| `format` | string | `"%(asctime)s - …"` | Log format string |
| `file` | string | `"logs/akiba_crypto_bot.log"` | Main log file path |
| `max_bytes` | int | `10485760` | Max log file size (10 MB) |
| `backup_count` | int | `5` | Number of rotated log files to keep |

### `backtesting`

| Key | Type | Default | Description |
|---|---|---|---|
| `timerange` | string | `"20230101-20231231"` | Date range for backtests |
| `initial_balance` | float | `10000` | Starting balance for simulations |
| `fee` | float | `0.001` | Simulated trading fee (0.1%) |

---

## `config/freqai_config.json`

Defines ML model hyperparameters for each supported model type.

| Model | Key Parameters |
|---|---|
| **LightGBMClassifier** | `n_estimators=100`, `learning_rate=0.1`, `max_depth=10` |
| **XGBoostClassifier** | `n_estimators=100`, `learning_rate=0.1`, `max_depth=6` |
| **CatBoostClassifier** | `n_estimators=100`, `learning_rate=0.1`, `depth=6` |
| **RandomForest** | `n_estimators=100`, `max_depth=10`, `random_state=42` |

> [!IMPORTANT]
> You must install the corresponding ML library (e.g., `xgboost`, `catboost`) separately if you want to use a model other than LightGBM or RandomForest.

---

## Config Validation

Run the config validator standalone to check for errors before starting:

```bash
python src/config_validator.py config/config.json
```

The validator checks all sections and reports errors (invalid/missing values) and warnings (sub-optimal settings).
