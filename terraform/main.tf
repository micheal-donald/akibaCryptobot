terraform {
  required_providers {
    vultr = {
      source  = "vultr/vultr"
      version = "~> 2.19"
    }
  }
  required_version = ">= 1.0"
}

# The provider expects the VULTR_API_KEY environment variable to be set
provider "vultr" {}

variable "allowed_ssh_ip" {
  description = "Your IP address for SSH access (CIDR format). Vultr handles this via firewall groups."
  type        = string
  default     = "0.0.0.0/0"
}

variable "ssh_public_key" {
  description = "Your public SSH key string (e.g., contents of ~/.ssh/id_rsa.pub)"
  type        = string
}

variable "instance_plan" {
  description = "Vultr Plan ID. Default is 2GB RAM / 1 vCPU Standard ($10/mo)"
  type        = string
  default     = "vc2-1c-2gb"
}

# Fetch the Ubuntu 22.04 OS ID dynamically
data "vultr_os" "ubuntu" {
  filter {
    name   = "name"
    values = ["Ubuntu 22.04 LTS x64"]
  }
}

# Register your SSH key with Vultr
resource "vultr_ssh_key" "bot_key" {
  name    = "akibabot-key"
  ssh_key = var.ssh_public_key
}

# Create a Firewall Group
resource "vultr_firewall_group" "bot_fw" {
  description = "AkibaBot Security Group"
}

# Allow SSH Access
resource "vultr_firewall_rule" "ssh_access" {
  firewall_group_id = vultr_firewall_group.bot_fw.id
  protocol          = "tcp"
  ip_type           = "v4"
  subnet           = split("/", var.allowed_ssh_ip)[0]
  subnet_size      = split("/", var.allowed_ssh_ip)[1]
  port              = "22"
}

# Allow API Access (FastAPI Dashboard)
resource "vultr_firewall_rule" "api_access" {
  firewall_group_id = vultr_firewall_group.bot_fw.id
  protocol          = "tcp"
  ip_type           = "v4"
  subnet           = split("/", var.allowed_ssh_ip)[0]
  subnet_size      = split("/", var.allowed_ssh_ip)[1]
  port              = "8000"
}

# Provision the Instance in Tokyo (nrt)
resource "vultr_instance" "trading_bot" {
  plan              = var.instance_plan
  region            = "nrt" # Tokyo, Japan (Lowest latency to Binance)
  os_id             = data.vultr_os.ubuntu.id
  label             = "akiba-cryptobot"
  hostname          = "akibabot"
  ssh_key_ids       = [vultr_ssh_key.bot_key.id]
  firewall_group_id = vultr_firewall_group.bot_fw.id

  # Cloud-init User data for initial setup parity with original AWS script
  user_data = <<-EOF
    #!/bin/bash
    set -e

    # Update system
    apt-get update
    apt-get upgrade -y

    # Install Python, Git, Docker
    apt-get install -y python3 python3-pip python3-venv git docker.io
    systemctl enable docker
    systemctl start docker

    # Create bot user
    useradd -m -s /bin/bash botuser
    usermod -aG docker botuser

    # Create app directory
    mkdir -p /opt/akibabot
    chown botuser:botuser /opt/akibabot

    echo "Initial setup complete. Deploy your bot to /opt/akibabot"
  EOF
}

output "instance_id" {
  description = "Vultr Instance ID"
  value       = vultr_instance.trading_bot.id
}

output "public_ip" {
  description = "Public IP address of the Bot"
  value       = vultr_instance.trading_bot.main_ip
}

output "ssh_command" {
  description = "SSH command to connect"
  value       = "ssh root@${vultr_instance.trading_bot.main_ip}"
}
