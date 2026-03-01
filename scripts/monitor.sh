#!/bin/bash
# AkibaCryptoBot Watchdog Monitor
# Checks systemd service status and memory usage. Sends Telegram alerts on failure.

# Configuration (Injected via environment or hardcoded deployment)
SERVICE_NAME="akibabot.service"
MAX_MEM_MB=1500 # 1.5GB limit (Vultr instance has 2GB total)
LOG_FILE="/opt/akibabot/logs/watchdog.log"

# Load environment variables for Telegram API
source /opt/akibabot/.env

# Validate required vars
if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    echo "$(date): ERROR - Telegram credentials missing in .env" >> "$LOG_FILE"
    exit 1
fi

# Function to send Telegram message
send_telegram() {
    local message="$1"
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TELEGRAM_CHAT_ID}" \
        -d text="${message}" \
        -d parse_mode="Markdown" > /dev/null
}

# 1. Check if service is running
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "$(date): WARNING - $SERVICE_NAME is not running. Attempting restart." >> "$LOG_FILE"
    send_telegram "🚨 *AkibaBot Watchdog Alert* 🚨%0A%0AThe systemd service \`$SERVICE_NAME\` has crashed or stopped. Attempting automatic restart now."
    
    systemctl restart "$SERVICE_NAME"
    sleep 5
    
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        send_telegram "✅ *Watchdog Recovery Successful*%0A%0ABot is back online."
    else
        send_telegram "❌ *Watchdog Recovery FAILED*%0A%0AImmediate manual intervention required on the Vultr server."
    fi
    exit 0
fi

# 2. Check Memory Usage
# Get Main PID
BOT_PID=$(systemctl show -p MainPID "$SERVICE_NAME" | cut -d= -f2)

if [ "$BOT_PID" -gt 0 ]; then
    # Get memory usage in MB (Resident Set Size)
    MEM_USAGE=$(ps -o rss= -p "$BOT_PID" | awk '{print int($1/1024)}')
    
    if [ "$MEM_USAGE" -gt "$MAX_MEM_MB" ]; then
        echo "$(date): WARNING - Memory usage ($MEM_USAGE MB) exceeded limit ($MAX_MEM_MB MB). Restarting." >> "$LOG_FILE"
        send_telegram "⚠️ *AkibaBot Memory Alert*%0A%0ABot memory usage (\`${MEM_USAGE} MB\`) exceeded safety limit (\`${MAX_MEM_MB} MB\`). Preemptive restart initiated to prevent OOM crash."
        systemctl restart "$SERVICE_NAME"
    fi
fi
