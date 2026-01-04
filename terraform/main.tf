terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile != "" ? var.aws_profile : null
}

# Discover the default VPC and subnets so a minimal config works out of the box.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

locals {
  # Choose a default subnet; prefer one not in us-east-1e to avoid instance-type support issues.
  filtered_default_subnets = [
    for s in data.aws_subnet.default_subnets :
    s.id if length(regexall("us-east-1e$", s.availability_zone)) == 0
  ]
  subnet_id = coalesce(
    var.subnet_id,
    try(element(local.filtered_default_subnets, 0), null),
    try(data.aws_subnets.default.ids[0], null)
  )
}

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

data "aws_subnet" "default_subnets" {
  for_each = toset(data.aws_subnets.default.ids)
  id       = each.value
}

resource "aws_security_group" "adt_press" {
  name        = "${var.project_name}-sg"
  description = "Allow SSH and app traffic"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_cidr]
  }

  ingress {
    description = "App"
    from_port   = var.app_port
    to_port     = var.app_port
    protocol    = "tcp"
    cidr_blocks = [var.http_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "adt_press" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.instance_type
  subnet_id                   = local.subnet_id
  vpc_security_group_ids      = [aws_security_group.adt_press.id]
  associate_public_ip_address = true
  key_name                    = var.key_name

  user_data = templatefile("${path.module}/user_data.sh", {
    app_port              = var.app_port
    repo_url              = var.repo_url
    ssh_private_key       = var.ssh_private_key
    backend_submodule_url = var.backend_submodule_url
    openai_api_key        = var.openai_api_key
    adt_api_key           = var.adt_api_key
  })

  tags = {
    Name = "${var.project_name}-server"
  }

  lifecycle {
    ignore_changes = [user_data] # allow tweaking user_data without recreation
  }
}

output "public_ip" {
  description = "Public IP of the ADT Press instance"
  value       = aws_instance.adt_press.public_ip
}

output "app_url" {
  description = "Convenience URL for the API"
  value       = "http://${aws_instance.adt_press.public_dns}:${var.app_port}"
}
