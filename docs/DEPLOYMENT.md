# CI/CD Deployment Guide

## GitHub Secrets Required

Go to your GitHub repository → Settings → Secrets and variables → Actions → New repository secret

### Required Secrets

| Secret | Description | Example |
|--------|-------------|---------|
| `EC2_HOST` | EC2 public IP or DNS | `54.178.xxx.xxx` |
| `SSH_PRIVATE_KEY` | EC2 SSH private key (full content) | `-----BEGIN RSA PRIVATE KEY-----...` |
| `BINANCE_API_KEY` | Binance API key | `aBcDeFgHiJk...` |
| `BINANCE_SECRET_KEY` | Binance secret key | `xYz123456...` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | `123456789:ABC...` |
| `TELEGRAM_CHAT_ID` | Telegram chat ID | `123456789` |
| `TRADING_MODE` | Trading mode | `paper` or `live` |

### How to Get SSH Private Key

After running Terraform:
```bash
# If you created a new key pair
cat ~/.ssh/akibabot-key.pem
```

Copy the entire content including `-----BEGIN` and `-----END` lines.

---

## Deployment Flow

```
Push to main → CI runs tests → Build Docker image → Push to GHCR → 
SSH to EC2 → Pull image → Restart container → Telegram notification
```

---

## First Time Setup

### 1. Run Terraform

```bash
cd terraform
terraform init
terraform apply
```

Note the `public_ip` from output.

### 2. SSH and Setup EC2

```bash
ssh -i ~/.ssh/akibabot-key.pem ubuntu@<public_ip>

# Run setup script (or manually)
curl -sSL https://raw.githubusercontent.com/YOUR_REPO/main/scripts/setup-ec2.sh | bash
```

### 3. Add GitHub Secrets

Add all secrets listed above to GitHub repository settings.

### 4. Push to Deploy

```bash
git add .
git commit -m "Initial deployment"
git push origin main
```

The CI/CD pipeline will automatically:
1. Run tests
2. Build Docker image
3. Deploy to EC2
4. Send Telegram notification

---

## Manual Deployment

If you need to deploy manually:

```bash
ssh -i ~/.ssh/akibabot-key.pem ubuntu@<ec2_ip>

cd /opt/akibabot
docker-compose pull
docker-compose up -d
docker-compose logs -f
```

---

## Troubleshooting

### Container not starting
```bash
docker-compose logs
```

### Check container status
```bash
docker-compose ps
```

### Restart container
```bash
docker-compose restart
```

### View bot logs
```bash
tail -f /opt/akibabot/logs/akiba_crypto_bot.log
```
