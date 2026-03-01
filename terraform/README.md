# Terraform Deployment for AkibaCryptoBot (Vultr)

## Prerequisites

1. Install Terraform: https://terraform.io/downloads
2. Create a Vultr Account: https://www.vultr.com/
3. Generate a Personal Access Token (API Key) from the Vultr API Dashboard.
4. Allow your IP address in the Vultr API Access Control settings if prompted.
5. Export your API key to your terminal:
   ```bash
   export VULTR_API_KEY="your-vultr-api-key"
   ```

## Quick Start
You must pass your public SSH key to the Terraform execution so Vultr can inject it into the server for secure access.

```bash
cd terraform

# Generate an SSH key if you don't have one
# ssh-keygen -t ed25519 -C "bot_key"

# Initialize Terraform
terraform init

# Deploy! (It will prompt you to enter your SSH Public Key string)
terraform apply
```
*Tip: You can avoid the prompt by passing it directly: `terraform apply -var="ssh_public_key=$(cat ~/.ssh/id_rsa.pub)"`*

## After Deployment

1. SSH into the server as root (or the `botuser` you create later):
   ```bash
   ssh root@<PUBLIC_IP>
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

## Destroy

```bash
terraform destroy
```
