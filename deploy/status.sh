#!/usr/bin/env bash
set -euo pipefail

# kindshot 서비스 상태 + 운영 모니터 요약
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="/opt/kindshot"
if [ ! -d "$APP_DIR" ]; then
    APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

echo "=== systemd status ==="
sudo systemctl status kindshot --no-pager 2>/dev/null || echo "(서비스 미등록)"

echo ""
echo "=== 모니터 요약 ==="
python3 "$APP_DIR/deploy/server_monitor.py" "$(TZ=Asia/Seoul date +%Y%m%d)" 2>/dev/null || echo "(모니터 요약 실패)"
