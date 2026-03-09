#!/usr/bin/env bash
set -euo pipefail

# 장중 자동 시작/종료 crontab 등록
# 평일만 (1-5), KST 기준

CRON_START="55 8 * * 1-5 systemctl start kindshot"
CRON_STOP="35 15 * * 1-5 systemctl stop kindshot"

# 기존 kindshot 관련 crontab 제거 후 재등록
(crontab -l 2>/dev/null | grep -v kindshot; echo "$CRON_START"; echo "$CRON_STOP") | sudo crontab -

echo "crontab 등록 완료:"
echo "  시작: 평일 08:55"
echo "  종료: 평일 15:35"
sudo crontab -l | grep kindshot
