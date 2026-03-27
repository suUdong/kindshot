#!/usr/bin/env bash
# Kindshot Dashboard 실행 스크립트
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$DIR")"

cd "$PROJECT_ROOT"

# venv 활성화 (있으면)
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
fi

exec streamlit run dashboard/app.py \
    --server.port "${DASHBOARD_PORT:-8501}" \
    --server.address "${DASHBOARD_HOST:-0.0.0.0}" \
    --browser.gatherUsageStats false \
    "$@"
