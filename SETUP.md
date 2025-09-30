# AkibaCryptoBot Setup Guide

## Prerequisites

1. **Python 3.9+** installed
2. **Binance Account** with API access
3. **Telegram Bot** (optional, for notifications)

## Installation Steps

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Environment Configuration

Copy the example environment file and configure it:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```bash
# Required - Binance API Configuration
BINANCE_API_KEY=your_binance_api_key_here
BINANCE_SECRET_KEY=your_binance_secret_key_here

# Optional - Telegram Bot Configuration  
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here

# Trading Configuration
TRADING_MODE=paper  # Start with paper trading
LOG_LEVEL=INFO
```

### 3. Get Binance API Keys

1. Go to [Binance API Management](https://www.binance.com/en/my/settings/api-management)
2. Create a new API key
3. **Enable "Spot & Margin Trading"** 
4. **Restrict to trusted IPs** (recommended)
5. Copy API Key and Secret Key to `.env`

⚠️ **Important**: Start with **Binance Testnet** for paper trading:
- Testnet: https://testnet.binance.vision/
- Get testnet API keys from https://testnet.binance.vision/

### 4. Setup Telegram Bot (Optional)

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Create a new bot with `/newbot`
3. Copy the bot token to `.env`
4. Get your chat ID:
   - Message your bot
   - Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
   - Find your chat ID in the response
   - Add it to `.env`

### 5. Configure Trading Parameters

Edit `config/config.json` to adjust:

```json
{
  "exchange": {
    "sandbox": true  // Set to false for live trading
  },
  "trading": {
    "pairs": ["BTC/USDT", "ETH/USDT"],  // Add/remove pairs
    "max_open_trades": 3,
    "stake_amount": 0.05  // 5% of balance per trade
  },
  "risk_management": {
    "max_position_size": 0.05,  // 5% max position
    "stop_loss_percent": 0.02,  // 2% stop loss
    "take_profit_percent": 0.04  // 4% take profit
  }
}
```

## Testing the Bot

### 1. Paper Trading Test

```bash
cd /home/lekim/Projects/akibaCryptobot
python src/bot.py
```

The bot will start in paper trading mode (sandbox) by default.

### 2. Check Logs

Monitor the bot's activity:

```bash
# Main logs
tail -f logs/akiba_crypto_bot.log

# Trade logs
tail -f logs/trades.log

# Error logs  
tail -f logs/errors.log
```

### 3. Telegram Commands (if configured)

- `/start` - Initialize bot
- `/status` - Check bot status
- `/balance` - View account balance
- `/trades` - See active trades
- `/stop` - Pause trading
- `/resume` - Resume trading

## Quick Start Checklist

- [ ] Python 3.9+ installed
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] `.env` file configured with Binance testnet API keys
- [ ] `config/config.json` reviewed and adjusted
- [ ] Telegram bot setup (optional)
- [ ] Bot started with `python src/bot.py`
- [ ] Logs directory created and logs flowing
- [ ] Telegram notifications working (if enabled)

## Safety Recommendations

1. **Start with testnet/sandbox** - Never use real money initially
2. **Small position sizes** - Start with 1-2% per trade
3. **Monitor closely** - Watch the first few hours/days
4. **Test stop losses** - Ensure risk management works
5. **Gradual scaling** - Increase position sizes slowly

## Troubleshooting

### Common Issues

**"Invalid API key"**
- Verify API key/secret in `.env`
- Check if using testnet keys with sandbox=true

**"Insufficient balance"**
- Add testnet funds from Binance testnet faucet
- Reduce position sizes in config

**"No module named 'ccxt'"**
- Run `pip install -r requirements.txt`
- Check Python version (3.9+ required)

**Telegram not working**
- Verify bot token and chat ID
- Check bot permissions
- Message the bot first before getting chat ID

### Log Locations

- Main bot logs: `logs/akiba_crypto_bot.log`
- Trade logs: `logs/trades.log`  
- Error logs: `logs/errors.log`
- Performance logs: `logs/performance.log`

## Next Steps

After successful paper trading:

1. Run for 24-48 hours in paper mode
2. Review performance logs and metrics
3. Adjust strategy parameters if needed
4. Consider implementing custom strategies
5. **Only then** switch to live trading with small amounts

## Support

Check logs for detailed error messages. Most issues are configuration-related.

For Binance API issues, refer to: https://binance-docs.github.io/apidocs/spot/en/