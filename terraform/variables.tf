variable "project_name" {
  description = "Name prefix for tags and resources."
  type        = string
  default     = "adt-press"
}

variable "aws_profile" {
  description = "Optional AWS CLI profile to use. Leave empty to use the default/ env credentials."
  type        = string
  default     = ""
}

variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "EC2 instance type."
  type        = string
  default     = "t3.medium"
}

variable "key_name" {
  description = "Existing EC2 key pair name for SSH access."
  type        = string
}

variable "subnet_id" {
  description = "Optional subnet ID; defaults to the first subnet in the default VPC."
  type        = string
  default     = null
}

variable "ssh_cidr" {
  description = "CIDR allowed to SSH."
  type        = string
  default     = "0.0.0.0/0"
}

variable "http_cidr" {
  description = "CIDR allowed to reach the API port."
  type        = string
  default     = "0.0.0.0/0"
}

variable "app_port" {
  description = "Port the FastAPI server listens on."
  type        = number
  default     = 8000
}

variable "repo_url" {
  description = "Git SSH URL for the adt-press repo (e.g., git@github.com:org/adt-press.git). Leave empty to copy code manually."
  type        = string
  default     = "git@github.com:unicef/adt-press.git"
}

variable "backend_submodule_url" {
  description = "Git SSH URL for the adt-backend submodule."
  type        = string
  default     = "git@github.com:unicef/adt-backend.git"
}

variable "ssh_private_key" {
  description = "Deploy key (PEM) used for cloning private repos over SSH. Sensitive; prefer a least-privilege deploy key."
  type        = string
  default     = ""
  sensitive   = true
}

variable "openai_api_key" {
  description = "OpenAI API key exported to the service environment."
  type        = string
  sensitive   = true
}

variable "adt_api_key" {
  description = "API key required by the backend (ADT_API_KEY env var)."
  type        = string
  default     = ""
  sensitive   = true
}
