#!/usr/bin/env bash
set -euo pipefail

# 수동 실행: venv activate → python -m kindshot
# 사용법: bash deploy/run.sh [--dry-run|--paper|--replay FILE]
APP_DIR="/opt/kindshot"

cd "$APP_DIR"
source .venv/bin/activate
exec python -m kindshot "$@"
