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

### Trading

| Command | Description |
|---|---|
| `/balance` | Live wallet balances from Binance |
| `/trades` | List open positions from the active source of truth, including runtime-projected positions during runtime-owned modes |
| `/stop` | Pause the trading engine (stops scanning for new trades) |
| `/resume` | Resume the trading engine |

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

### Trade notification
```
📈 NEW TRADE — BUY
━━━━━━━━━━━━━━━━━━━━━
Symbol: BTC/USDT
Side: BUY
Amount: 0.001 BTC
Entry: $42,500.00
Stop Loss: $41,650.00
Take Profit: $44,200.00
Signal Strength: 0.72
```
