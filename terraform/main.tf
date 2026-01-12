terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
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

# Random suffix for unique S3 bucket name
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# S3 bucket for job output zips
resource "aws_s3_bucket" "adt_outputs" {
  bucket = "${var.project_name}-outputs-${random_id.bucket_suffix.hex}"
}

# Bucket lifecycle - expire objects after 7 days
resource "aws_s3_bucket_lifecycle_configuration" "adt_outputs" {
  bucket = aws_s3_bucket.adt_outputs.id

  rule {
    id     = "expire-old-zips"
    status = "Enabled"
    expiration {
      days = 7
    }
  }
}

# CORS configuration to allow browser downloads via fetch()
resource "aws_s3_bucket_cors_configuration" "adt_outputs" {
  bucket = aws_s3_bucket.adt_outputs.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = ["*"] # Allow all origins for simplicity, or restrict to specific domains
    expose_headers  = []
    max_age_seconds = 3000
  }
}

# IAM role for EC2 to access S3
resource "aws_iam_role" "adt_press_ec2" {
  name = "${var.project_name}-ec2-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "s3_access" {
  name = "${var.project_name}-s3-access"
  role = aws_iam_role.adt_press_ec2.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject", "s3:GetObject"]
      Resource = "${aws_s3_bucket.adt_outputs.arn}/*"
    }]
  })
}

resource "aws_iam_instance_profile" "adt_press" {
  name = "${var.project_name}-instance-profile"
  role = aws_iam_role.adt_press_ec2.name
}

resource "aws_instance" "adt_press" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.instance_type
  subnet_id                   = local.subnet_id
  vpc_security_group_ids      = [aws_security_group.adt_press.id]
  associate_public_ip_address = true
  key_name                    = var.key_name
  iam_instance_profile        = aws_iam_instance_profile.adt_press.name

  user_data = templatefile("${path.module}/user_data.sh", {
    app_port              = var.app_port
    repo_url              = var.repo_url
    ssh_private_key       = var.ssh_private_key
    backend_submodule_url = var.backend_submodule_url
    openai_api_key        = var.openai_api_key
    adt_api_key           = var.adt_api_key
    s3_bucket_name        = aws_s3_bucket.adt_outputs.bucket
  })

  tags = {
    Name = "${var.project_name}-server"
  }

  lifecycle {
    ignore_changes = [user_data] # allow tweaking user_data without recreation
  }
}

resource "aws_eip" "adt_press" {
  instance = aws_instance.adt_press.id
  domain   = "vpc"
}

resource "aws_cloudfront_distribution" "adt_press_api" {
  origin {
    domain_name = aws_eip.adt_press.public_dns
    origin_id   = "adt-press-origin"

    custom_origin_config {
      http_port              = var.app_port
      https_port             = var.app_port
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  enabled         = true
  is_ipv6_enabled = true
  comment         = "ADT Press API (HTTPS)"

  default_cache_behavior {
    allowed_methods  = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "adt-press-origin"

    forwarded_values {
      query_string = true
      headers      = ["*"]
      
      cookies {
        forward = "all"
      }
    }

    viewer_protocol_policy = "redirect-to-https"
    min_ttl                = 0
    default_ttl            = 0
    max_ttl                = 0
  }

  price_class = "PriceClass_100"

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

output "public_ip" {
  description = "Public IP (Elastic IP) of the ADT Press instance"
  value       = aws_eip.adt_press.public_ip
}

output "app_url_http" {
  description = "Direct HTTP Access (Port 8000)"
  value       = "http://${aws_eip.adt_press.public_dns}:${var.app_port}"
}

output "app_url_https" {
  description = "CloudFront HTTPS Access (Port 443)"
  value       = "https://${aws_cloudfront_distribution.adt_press_api.domain_name}"
}

output "s3_bucket_name" {
  description = "S3 bucket for job output zips"
  value       = aws_s3_bucket.adt_outputs.bucket
}
