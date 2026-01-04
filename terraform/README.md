# Terraform: ADT Press EC2

Minimal Terraform to spin up a single EC2 host for the ADT Press backend API.

## Files
- `main.tf` — EC2 instance, security group, and outputs.
- `variables.tf` — tweak region, instance type, CIDRs, etc.
- `user_data.sh` — bootstrap script that (optionally) clones your repo, builds a venv, and starts Uvicorn.

## Usage
1. Ensure you have an EC2 key pair (`var.key_name`) and AWS credentials configured. If you use a specific CLI profile, set `aws_profile` or export `AWS_PROFILE`. If your default subnet lands in an unsupported AZ (e.g., us-east-1e for certain instance types), either supply `subnet_id` or rerun with the default selection (this config already prefers non-1e subnets).
2. Set `repo_url` to your adt-press repo (SSH). The script runs `git submodule update --init --recursive` and will override the `adt-backend` submodule URL to `backend_submodule_url` (default is `git@github.com:unicef/adt-backend.git`).
3. Provide an SSH deploy key via `ssh_private_key` (least-privilege). This key is written to `/opt/adt-press/.ssh/id_ed25519` with strict perms and used for both the main repo and submodules. If you prefer another auth method (e.g., SSM, baked AMI keys), leave it empty and handle auth yourself.
4. Set `openai_api_key` and `adt_api_key` so the service can call OpenAI and require an API key for access.
3. Init and apply:
   ```bash
   cd terraform
   terraform init
   terraform apply \
     -var="key_name=your-keypair" \
     -var="region=us-east-1" \
     -var="repo_url=git@github.com:unicef/adt-press.git" \
     -var="backend_submodule_url=git@github.com:unicef/adt-backend.git" \
     -var="ssh_private_key=$(cat /path/to/your/deploy_key.pem)" \
     -var="openai_api_key=sk-..." \
     -var="adt_api_key=your-api-key"
   ```
4. Once applied, Terraform prints `app_url` (defaults to port 8000).

## Notes
- Security group opens SSH (22) to `ssh_cidr` and the app port to `http_cidr` (defaults are wide open; tighten for production).
- Uses the default VPC/subnet unless you supply `subnet_id`.
- The service waits for `/opt/adt-press/adt-backend/.venv/bin/uvicorn`; it only starts automatically when the repo is present and dependencies are installed.
- The API requires `ADT_API_KEY` unless left blank; send it via `x-api-key` or `Authorization: Bearer ...`.
- Bootstrap is idempotent: if `/opt/adt-press` exists but is not a git repo (e.g., a failed run), it will be cleaned and recloned.
