#!/usr/bin/env bash
# One-time server setup. Run from the project folder:   bash deploy/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
USER_NAME="$(whoami)"

echo "== Time zone -> Europe/Copenhagen"
sudo timedatectl set-timezone Europe/Copenhagen

echo "== Swap (small free servers have only 1 GB RAM)"
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 1G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== System packages"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip

echo "== Python environment (takes a few minutes)"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

echo "== Protect personal files"
chmod 600 settings.toml tesla_secrets.toml tokens.json private-key.pem 2>/dev/null || true

echo "== Services"
for svc in ev-logger ev-dashboard; do
  sed -e "s|__USER__|$USER_NAME|g" -e "s|__DIR__|$APP_DIR|g" "deploy/$svc.service" \
    | sudo tee "/etc/systemd/system/$svc.service" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now ev-logger ev-dashboard

echo
echo "Done. Open the dashboard on port 8501 and follow the setup page."
