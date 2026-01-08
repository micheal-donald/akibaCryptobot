# Terraform Deployment for AkibaCryptoBot

## Prerequisites

1. Install Terraform: https://terraform.io/downloads
2. Configure AWS credentials:
   ```bash
   export AWS_ACCESS_KEY_ID="your-access-key"
   export AWS_SECRET_ACCESS_KEY="your-secret-key"
   ```
3. Create an EC2 key pair in AWS Console (Tokyo region)

## Quick Start

```bash
cd terraform

# Copy and edit variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your settings

# Initialize Terraform
terraform init

# Preview changes
terraform plan

# Deploy
terraform apply
```

## After Deployment

1. SSH into the server:
   ```bash
   ssh -i ~/.ssh/akibabot-key.pem ubuntu@<PUBLIC_IP>
   ```

2. Clone and setup bot:
   ```bash
   cd /opt/akibabot
   git clone <your-repo> .
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. Configure environment:
   ```bash
   cp .env.example .env
   nano .env  # Add your API keys
   ```

4. Run the bot:
   ```bash
   python start_bot.py
   ```

## Costs

| Resource | Free Tier | After Free Tier |
|----------|-----------|-----------------|
| t3.micro | 750 hrs/month free | ~$8/month |
| EBS 20GB | 30 GB free | ~$2/month |
| Data transfer | 100 GB free | ~$1/month |
| **Total** | **$0** | **~$10/month** |

## Destroy

```bash
terraform destroy
```
