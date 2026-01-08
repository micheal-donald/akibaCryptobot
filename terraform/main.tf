# AkibaCryptoBot - AWS Tokyo Deployment
# Cheapest option: t3.micro (free tier eligible)

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  required_version = ">= 1.0"
}

provider "aws" {
  region = "ap-northeast-1"  # Tokyo - closest to Binance servers
  
  # Credentials from environment variables:
  # AWS_ACCESS_KEY_ID
  # AWS_SECRET_ACCESS_KEY
}

# Variables
variable "allowed_ssh_ip" {
  description = "Your IP address for SSH access (CIDR format)"
  type        = string
  default     = "0.0.0.0/0"  # Change this to your IP for security
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.micro"  # Free tier eligible
}

variable "key_name" {
  description = "Name of an existing EC2 key pair"
  type        = string
  default     = "akibabot-key"
}

# Data sources
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]  # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Security Group
resource "aws_security_group" "bot_sg" {
  name        = "akibabot-sg"
  description = "Security group for AkibaCryptoBot"

  # SSH access
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_ip]
    description = "SSH access"
  }

  # Outbound internet access (required for Binance API)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  tags = {
    Name    = "akibabot-sg"
    Project = "AkibaCryptoBot"
  }
}

# EC2 Instance
resource "aws_instance" "trading_bot" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = var.instance_type
  key_name      = var.key_name

  vpc_security_group_ids = [aws_security_group.bot_sg.id]

  # Root volume
  root_block_device {
    volume_size = 20  # 20 GB
    volume_type = "gp3"
    encrypted   = true
  }

  # User data script for initial setup
  user_data = <<-EOF
    #!/bin/bash
    set -e
    
    # Update system
    apt-get update
    apt-get upgrade -y
    
    # Install Python and dependencies
    apt-get install -y python3 python3-pip python3-venv git
    
    # Install Docker (optional, for containerized deployment)
    apt-get install -y docker.io
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

  tags = {
    Name    = "akiba-cryptobot"
    Project = "AkibaCryptoBot"
    Environment = "production"
  }
}

# Elastic IP (optional - for static IP)
resource "aws_eip" "bot_eip" {
  instance = aws_instance.trading_bot.id
  domain   = "vpc"

  tags = {
    Name    = "akibabot-eip"
    Project = "AkibaCryptoBot"
  }
}

# Outputs
output "instance_id" {
  description = "EC2 Instance ID"
  value       = aws_instance.trading_bot.id
}

output "public_ip" {
  description = "Public IP address (Elastic IP)"
  value       = aws_eip.bot_eip.public_ip
}

output "ssh_command" {
  description = "SSH command to connect"
  value       = "ssh -i ~/.ssh/${var.key_name}.pem ubuntu@${aws_eip.bot_eip.public_ip}"
}

output "estimated_monthly_cost" {
  description = "Estimated monthly cost"
  value       = "~$0 (free tier) or ~$8-10/month after free tier"
}
