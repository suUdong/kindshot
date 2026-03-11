#!/usr/bin/env bash
# 서버에서 로그 파일을 GitHub에 push하는 스크립트
# 사용: bash deploy/push-logs.sh
#
# 사전 설정 (1회):
#   echo "https://<GITHUB_USER>:<GITHUB_PAT>@github.com" > /opt/kindshot/.git-credentials
#   chmod 600 /opt/kindshot/.git-credentials
#   cd /opt/kindshot && git config credential.helper 'store --file=/opt/kindshot/.git-credentials'

set -euo pipefail

REPO_DIR="/opt/kindshot"
cd "$REPO_DIR"

# credential helper 설정 확인
if ! git config credential.helper | grep -q store; then
    echo "ERROR: git credential helper not configured."
    echo "Run:"
    echo "  echo 'https://<USER>:<PAT>@github.com' > $REPO_DIR/.git-credentials"
    echo "  chmod 600 $REPO_DIR/.git-credentials"
    echo "  git config credential.helper 'store --file=$REPO_DIR/.git-credentials'"
    exit 1
fi

# daily report 생성
DATE=$(TZ=Asia/Seoul date +%Y%m%d)
python3 deploy/daily_report.py "$DATE" > "logs/daily_report_${DATE}.txt" 2>&1 || true

# 로그 파일만 add (-f: .gitignore에 logs/ 있으므로 강제)
git add -f logs/*.jsonl logs/polling_trace_*.jsonl 2>/dev/null || true
git add -f logs/unknown_headlines/*.jsonl 2>/dev/null || true
git add -f logs/daily_report_*.txt 2>/dev/null || true

# 변경 없으면 종료
if git diff --cached --quiet; then
    echo "No new log files to push."
    exit 0
fi

git commit -m "chore: add ${DATE} logs"
git push origin main

echo "Logs pushed successfully."
