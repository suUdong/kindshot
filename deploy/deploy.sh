#!/usr/bin/env bash
set -euo pipefail

# 원커맨드 배포: git pull → pip install → restart
APP_DIR="/opt/kindshot"

cd "$APP_DIR"
echo "=== git pull ==="
git pull

echo "=== pip install ==="
source .venv/bin/activate
pip install -e . --quiet

echo "=== restart service ==="
sudo systemctl restart kindshot
sudo systemctl status kindshot --no-pager

echo "=== 배포 완료 ==="
