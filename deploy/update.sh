#!/usr/bin/env bash
# Update to the latest version from GitHub:   bash deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
.venv/bin/pip install --quiet -r requirements.txt
sudo systemctl restart ev-logger ev-dashboard
echo "Updated and restarted."
