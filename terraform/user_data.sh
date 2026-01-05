#!/bin/bash
set -euxo pipefail

APP_PORT="${app_port}"
REPO_URL="${repo_url}"
SSH_PRIVATE_KEY="${ssh_private_key}"
BACKEND_URL="${backend_submodule_url}"
OPENAI_API_KEY="${openai_api_key}"
ADT_API_KEY="${adt_api_key}"

export HOME=/root

LOG_FILE="/var/log/adt-press-bootstrap.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== ADT Press Bootstrap Started at $(date) ==="

# Update system and install dependencies
echo "Installing system dependencies..."
yum update -y
echo "Installing system dependencies..."
yum update -y
yum groupinstall -y "Development Tools"
# Split installs to ensure we know what fails
yum install -y git cairo-devel pango-devel pkg-config mesa-libGL libglvnd-glx 
yum install -y nodejs npm

# Install ffmpeg (static build)
echo "Installing ffmpeg..."
cd /tmp
curl -O https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
tar -xf ffmpeg-release-amd64-static.tar.xz
mv ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/
mv ffmpeg-*-amd64-static/ffprobe /usr/local/bin/
rm -rf ffmpeg-*

# Install uv package manager
echo "Installing uv..."
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"

# Create app user
if ! id adt >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/adt adt
fi

# Repo root
APP_ROOT="/opt/adt-press"

# Clean up failed previous attempts
if [ -d "$APP_ROOT" ] && [ ! -d "$APP_ROOT/.git" ]; then
  rm -rf "$APP_ROOT"
fi

# SSH setup (only if SSH key is provided for private repos)
SSH_DIR="/home/adt/.ssh"
USE_SSH=false

if [ -n "$SSH_PRIVATE_KEY" ]; then
  install -d -m 700 -o adt -g adt "$SSH_DIR"
  cat >"$SSH_DIR/id_ed25519" <<EOFKEY
$SSH_PRIVATE_KEY
EOFKEY
  chown adt:adt "$SSH_DIR/id_ed25519"
  chmod 600 "$SSH_DIR/id_ed25519"
  sudo -u adt ssh-keyscan github.com >>"$SSH_DIR/known_hosts" 2>/dev/null || true
  USE_SSH=true
  echo "SSH key configured for private repo access"
else
  echo "No SSH key provided - using HTTPS for public repos"
fi

# Clone the main repository
if [ -n "$REPO_URL" ] && [ ! -d "$APP_ROOT/.git" ]; then
  echo "Cloning main repository..."
  install -d -o adt -g adt "$APP_ROOT"

  if [ "$USE_SSH" = true ]; then
    export GIT_SSH_COMMAND="ssh -i $SSH_DIR/id_ed25519 -o StrictHostKeyChecking=accept-new"
    sudo -u adt GIT_SSH_COMMAND="$GIT_SSH_COMMAND" git clone "$REPO_URL" "$APP_ROOT"
  else
    sudo -u adt git clone "$REPO_URL" "$APP_ROOT"
  fi
fi

# Clone the backend repository (it may not be a submodule)
APP_DIR="$APP_ROOT/adt-backend"
if [ -n "$BACKEND_URL" ] && [ ! -d "$APP_DIR/.git" ]; then
  echo "Cloning backend repository..."
  rm -rf "$APP_DIR" 2>/dev/null || true
  if [ "$USE_SSH" = true ]; then
    sudo -u adt GIT_SSH_COMMAND="$GIT_SSH_COMMAND" git clone "$BACKEND_URL" "$APP_DIR"
  else
    sudo -u adt git clone "$BACKEND_URL" "$APP_DIR"
  fi
fi

# Add git safe directory for root operations
git config --global --add safe.directory "$APP_DIR"
git config --global --add safe.directory "$APP_ROOT"

# Install dependencies using uv
echo "Installing Python dependencies with uv..."
cd "$APP_ROOT"

# Sync the main adt-press package (this installs Python 3.13 and all deps)
/root/.local/bin/uv sync --python 3.13

# Move Python to a shared location so adt user can access it
PYTHON_SRC=$(find /root/.local/share/uv/python -name "cpython-3.13*" -type d | head -1)
if [ -n "$PYTHON_SRC" ]; then
  mkdir -p /opt/python
  cp -a "$PYTHON_SRC" /opt/python/
  PYTHON_DEST="/opt/python/$(basename $PYTHON_SRC)"
  chmod -R o+rx /opt/python
  
  # Update venv symlink
  rm -f "$APP_ROOT/.venv/bin/python"
  ln -s "$PYTHON_DEST/bin/python3.13" "$APP_ROOT/.venv/bin/python"
fi

# Install the backend package
echo "Installing adt-backend..."
/root/.local/bin/uv pip install "$APP_DIR"

# Fix ownership
chown -R adt:adt "$APP_ROOT"

# Create output directories
install -d -o adt -g adt "$APP_ROOT/output"
install -d -o adt -g adt "$APP_ROOT/uploads"

# Create systemd service - IMPORTANT: WorkingDirectory must be APP_ROOT for prompts paths
echo "Creating systemd service..."
cat >/etc/systemd/system/adt-press.service <<EOF
[Unit]
Description=ADT Press backend API
After=network.target

[Service]
User=adt
WorkingDirectory=$APP_ROOT
Environment=APP_PORT=$APP_PORT
Environment=OUTPUT_DIR=$APP_ROOT/output
Environment=UPLOAD_DIR=$APP_ROOT/uploads
Environment=OPENAI_API_KEY=$OPENAI_API_KEY
Environment=ADT_API_KEY=$ADT_API_KEY
Environment=ADT_PRESS_CONFIG_PATH=$APP_ROOT/config/config.yaml
ExecStart=$APP_ROOT/.venv/bin/uvicorn adt-backend.src.adt_press_backend.main:app --host 0.0.0.0 --port $APP_PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable adt-press.service
systemctl start adt-press.service

echo "=== ADT Press Bootstrap Completed at $(date) ==="
echo "Service status:"
systemctl status adt-press.service --no-pager || true

# Wait for service to be ready and test
sleep 5
curl -s http://localhost:$APP_PORT/healthz || echo "Health check pending..."
