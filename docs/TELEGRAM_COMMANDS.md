# Telegram Bot Commands

AkibaCryptoBot includes a Telegram bot for real-time monitoring and control. The bot sends automatic notifications for trades, errors, and daily reports, and responds to commands.

---

## Setup

1. **Create a bot** — talk to [@BotFather](https://t.me/BotFather) on Telegram, use `/newbot`, and copy the token.
2. **Get your chat ID** — talk to [@userinfobot](https://t.me/userinfobot) to get your numeric chat ID.
3. **Configure** — add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
   TELEGRAM_CHAT_ID=987654321
   ```
4. **Enable** in `config/config.json`:
   ```json
   {
     "notifications": {
       "telegram_enabled": true,
       "trade_notifications": true,
       "error_notifications": true,
       "daily_reports": true
     }
   }
   ```

---

## Command Reference

### General

| Command | Description |
|---|---|
| `/start` | Welcome message and bot info |
| `/help` | List all available commands |
| `/status` | Bot status — uptime, active trades or projected positions, runtime mode, source-of-truth state, and rollout guidance |
| `/config` | Current configuration summary |
| `/reconcile` | Manual balance audit vs Exchange |
| `/model` | Current FreqAI model health plus the latest training/reload snapshot |
| `/train_status` | Current and last server-side training job state |
| `/risk` | Risk report plus any active operational halt reason and blocked-entry summary |

### Trading

| Command | Description |
|---|---|
| `/balance` | Live wallet balances from Binance |
| `/trades` | List open positions from the active source of truth, including runtime-projected positions during runtime-owned modes |
| `/stop` | Pause new entries while leaving the bot lifecycle, exits, and API online |
| `/resume` | Resume new entries |
| `/train_model` | Queue a server-side FreqAI training job. Works only in paper mode while trading is paused |

### Performance

| Command | Description |
|---|---|
| `/performance` | Quick performance summary — total trades, win rate, PnL, open positions, and runtime ownership context |
| `/metrics` | Detailed metrics — Sharpe ratio, Sortino ratio, max drawdown, profit factor |
| `/report` | Full formatted performance report |

### Risk Management

| Command | Description |
|---|---|
| `/risk` | Comprehensive risk report — daily PnL, position exposure, drawdown, portfolio risk |
| `/correlations` | Asset correlation matrix for currently tracked pairs |
| `/heatmap` | Portfolio exposure heatmap showing risk concentration across correlated assets |

---

## Automatic Notifications

The bot sends these notifications automatically (when enabled in config):

| Notification | Trigger | Content |
|---|---|---|
| **Trade Entry** | A new position is opened | Symbol, side, amount, entry price, stop loss, take profit |
| **Trade Exit** | A position is closed | Symbol, exit price, PnL, exit reason |
| **Training Job** | A server-side model training job changes state | Job id, requester, symbols, timeframe, progress, result or error |
| **Operational Alert** | A sustained runtime/model/training issue is detected or cleared | Alert codes, halt state, blocked-entry reason, recommended action |
| **Error Alert** | An error occurs | Error message and context |
| **Daily Report** | End of trading day | Trades count, win rate, total PnL, drawdown |

---

## Example Messages

### `/status` response
```
📊 AkibaCryptoBot Status
━━━━━━━━━━━━━━━━━━━━━
🟢 Running
⏱ Uptime: 2h 15m
💼 Active Trades: 2
🔄 Trading Mode: paper
⚙️ Runtime Mode: runtime_primary
🧭 State Source: runtime_projection
🟢 Runtime Healthy: yes
🚦 Runtime Start Status: running
📣 Rollout Health: promotion_ready
➡️ Recommended Action: stay_runtime_primary
```

During a fresh `paper_shadow` rollout, `/status` may briefly report:

- `Runtime Running: no`
- `Runtime Start Status: configured` or `starting`
- `Rollout Health: startup_pending`
- `Recommended Action: wait_for_runtime`

That means the runtime is still warming up. It is not a rollback signal by itself.

When a reviewed live profile is active, `/status` also reports the rollout environment and stage explicitly. Typical live values are:

- `Deployment Profile: live_shadow`
- `Rollout Environment: live`
- `Rollout Stage: canary`
- `Rollout Recommended Action: promote_live_runtime_entries` or `promote_live_runtime_primary`

If live runtime health degrades, the recommended action becomes `rollback_to_live_legacy`.

### Trade notification
```
🟢⬆️ NEW LONG ENTRY
━━━━━━━━━━━━━━━━━━━━━
Symbol: BTC/USDT
Side: BUY
Amount: 0.001 BTC
Entry: $42,500.00
Stop Loss: $41,650.00
Take Profit: $44,200.00
Signal Strength: 0.72
```

Icon semantics used by the notifier:

- `🟢⬆️` buy/open long
- `🔴⬇️` sell/open short
- `✅📈` profitable exit
- `🛑📉` losing exit or stop
- `🧠⏳` training queued
- `🧠⚙️` training running
- `🧠✅` training succeeded
- `🧠❌` training failed

### `/train_model` response
```
🧠⏳ TRAINING JOB QUEUED
Job ID: job-42
Requested By: telegram
Pairs: BTC/USDT, ETH/USDT
Timeframe: 1h
Candles: 1000
```

The command shares the exact same guardrails as `POST /api/model/train`:

- paper mode only
- trading must already be paused
- only one queued/running job at a time
- the operator must fix the blocker before a new job can be queued

### Operational Alert and Halt Messages

When a critical operational alert appears, the bot sends a short alert transition message and the `/status` output reflects the same blocked-entry state shown by the API:

- `halt_state=entries_blocked`, `runtime_kill_switch`, or `legacy_circuit_breaker` means new entries are blocked
- exits continue to be managed by the runtime or legacy path that already owns them
- `/risk` shows the active alert code and the reason entries are blocked
- clearing the alert sends a recovery message and only then does the entry block clear
