#!/bin/bash
# Setup script for first-time EC2 deployment
# Run this on the EC2 instance after Terraform creates it

set -e

echo "🚀 Setting up AkibaCryptoBot on EC2..."

# Update system
sudo apt-get update
sudo apt-get upgrade -y

# Install Docker
if ! command -v docker &> /dev/null; then
    echo "📦 Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker ubuntu
    rm get-docker.sh
fi

# Install Docker Compose
if ! command -v docker-compose &> /dev/null; then
    echo "📦 Installing Docker Compose..."
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
fi

# Create app directory
sudo mkdir -p /opt/akibabot
sudo chown ubuntu:ubuntu /opt/akibabot
cd /opt/akibabot

# Create necessary directories
mkdir -p logs data models

# Create docker-compose.yml
cat > docker-compose.yml << 'EOF'
version: '3.8'

services:
  bot:
    image: ghcr.io/${GITHUB_REPO:-micheal-donald/akibacryptobot}:latest
    container_name: akiba-cryptobot
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
      - ./models:/app/models
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 1G
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
EOF

echo ""
echo "✅ Setup complete!"
echo ""
echo "📋 Next steps:"
echo "1. Create .env file with your credentials:"
echo "   nano /opt/akibabot/.env"
echo ""
echo "2. Add the following variables:"
echo "   TRADING_MODE=paper"
echo "   BINANCE_API_KEY=your_key"
echo "   BINANCE_SECRET_KEY=your_secret"
echo "   TELEGRAM_BOT_TOKEN=your_token"
echo "   TELEGRAM_CHAT_ID=your_chat_id"
echo ""
echo "3. Pull and run the container:"
echo "   docker pull ghcr.io/micheal-donald/akibacryptobot:latest"
echo "   docker-compose up -d"
echo ""
echo "4. Check logs:"
echo "   docker-compose logs -f"
