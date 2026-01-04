#!/bin/bash
set -euxo pipefail

APP_PORT="${app_port}"
REPO_URL="${repo_url}"
SSH_PRIVATE_KEY="${ssh_private_key}"
BACKEND_URL="${backend_submodule_url}"
OPENAI_API_KEY="${openai_api_key}"
ADT_API_KEY="${adt_api_key}"

echo "Configuring ADT Press host..." | tee /var/log/adt-press-bootstrap.log

yum update -y
yum install -y git python3 python3-pip python3-virtualenv openssh-clients

# Create app user and home (use /home/adt to avoid colliding with the repo path)
if ! id adt >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/adt adt
fi

# Repo root
APP_ROOT="/opt/adt-press"

# If the app dir exists but isn't a git repo (e.g., previous failed bootstrap), clean it up.
if [ -d "$APP_ROOT" ] && [ ! -d "$APP_ROOT/.git" ]; then
  rm -rf "$APP_ROOT"
fi

# SSH setup for the adt user
SSH_DIR="/home/adt/.ssh"
install -d -m 700 -o adt -g adt "$SSH_DIR"

if [ -n "$SSH_PRIVATE_KEY" ]; then
  cat >"$SSH_DIR/id_ed25519" <<EOFKEY
$SSH_PRIVATE_KEY
EOFKEY
  chown adt:adt "$SSH_DIR/id_ed25519"
  chmod 600 "$SSH_DIR/id_ed25519"
  sudo -u adt ssh-keyscan github.com >>"$SSH_DIR/known_hosts"
fi

if [ -n "$REPO_URL" ] && [ ! -d "$APP_ROOT/.git" ]; then
  # Create an empty repo directory owned by adt so git clone can write into /opt.
  install -d -o adt -g adt "$APP_ROOT"

  export GIT_SSH_COMMAND="ssh -i $SSH_DIR/id_ed25519 -o StrictHostKeyChecking=yes"
  sudo -u adt GIT_SSH_COMMAND="$GIT_SSH_COMMAND" git clone "$REPO_URL" "$APP_ROOT"

  # If the submodule URL needs overriding, set it before init.
  if [ -n "$BACKEND_URL" ] && [ -f "$APP_ROOT/.gitmodules" ]; then
    sudo -u adt git -C "$APP_ROOT" submodule set-url adt-backend "$BACKEND_URL" || true
  fi

  sudo -u adt GIT_SSH_COMMAND="$GIT_SSH_COMMAND" git -C "$APP_ROOT" submodule update --init --recursive || true
fi

APP_DIR="$APP_ROOT/adt-backend"
if [ -d "$APP_DIR" ]; then
  cd "$APP_DIR"
  sudo -u adt python3 -m venv .venv
  sudo -u adt /bin/bash -c ". .venv/bin/activate && pip install --upgrade pip && pip install ."
fi

cat >/etc/systemd/system/adt-press.service <<EOF
[Unit]
Description=ADT Press backend API
After=network.target
ConditionPathExists=$APP_DIR/.venv/bin/uvicorn

[Service]
User=adt
WorkingDirectory=$APP_DIR
Environment=APP_PORT=$APP_PORT
Environment=OUTPUT_DIR=/opt/adt-press/output
Environment=UPLOAD_DIR=/opt/adt-press/uploads
Environment=OPENAI_API_KEY=$OPENAI_API_KEY
Environment=ADT_API_KEY=$ADT_API_KEY
ExecStart=$APP_DIR/.venv/bin/uvicorn src.adt_press_backend.main:app --host 0.0.0.0 --port $APP_PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable adt-press.service || true
# Service will only start automatically if the repo was cloned and venv built.
systemctl start adt-press.service || true
