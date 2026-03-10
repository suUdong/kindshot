#!/usr/bin/env bash
set -euo pipefail

# kindshot 로그 실시간 확인 / 조회
# 사용법:
#   bash deploy/logs.sh           # 오늘 로그 tail -f
#   bash deploy/logs.sh journal   # journalctl 실시간
#   bash deploy/logs.sh summary   # 오늘 로그 요약 (log_summary.py)
#   bash deploy/logs.sh 20260310  # 특정 날짜 로그 tail

APP_DIR="/opt/kindshot"
MODE="${1:-}"

case "$MODE" in
    journal)
        sudo journalctl -u kindshot -f
        ;;
    summary)
        LOG_FILE="$APP_DIR/logs/kindshot_$(date -u +%Y%m%d).jsonl"
        if [ -f "$LOG_FILE" ]; then
            source "$APP_DIR/.venv/bin/activate"
            python "$APP_DIR/deploy/log_summary.py" "$LOG_FILE"
        else
            echo "오늘 로그 파일 없음"
        fi
        ;;
    [0-9]*)
        LOG_FILE="$APP_DIR/logs/kindshot_${MODE}.jsonl"
        if [ -f "$LOG_FILE" ]; then
            tail -20 "$LOG_FILE"
        else
            echo "로그 파일 없음: $LOG_FILE"
        fi
        ;;
    *)
        LOG_FILE="$APP_DIR/logs/kindshot_$(date -u +%Y%m%d).jsonl"
        if [ -f "$LOG_FILE" ]; then
            tail -f "$LOG_FILE"
        else
            echo "오늘 로그 파일 없음, journalctl로 대체"
            sudo journalctl -u kindshot -f
        fi
        ;;
esac
