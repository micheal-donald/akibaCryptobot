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
| `MAX_POSITION_SIZE` | Max portfolio fraction per trade | `0.05` |
| `MAX_DAILY_LOSS` | Max daily loss fraction | `0.02` |
| `STOP_LOSS_PERCENT` | Default stop-loss percentage | `0.02` |
| `TAKE_PROFIT_PERCENT` | Default take-profit percentage | `0.04` |
| `VULTR_API_KEY` | Vultr API key (for cloud deployment) | — |

> [!TIP]
> Copy `.env.example` to `.env` and fill in your credentials. Never commit `.env` to version control.

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
