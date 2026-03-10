#!/usr/bin/env bash
set -euo pipefail

# kindshot 서비스 상태 + 최근 로그 확인
APP_DIR="/opt/kindshot"

echo "=== systemd status ==="
sudo systemctl status kindshot --no-pager 2>/dev/null || echo "(서비스 미등록)"

echo ""
echo "=== 최근 journalctl (20줄) ==="
sudo journalctl -u kindshot --no-pager -n 20 2>/dev/null || echo "(로그 없음)"

echo ""
echo "=== 오늘 JSONL 로그 요약 ==="
LOG_FILE="$APP_DIR/logs/kindshot_$(date -u +%Y%m%d).jsonl"
if [ -f "$LOG_FILE" ]; then
    TOTAL=$(wc -l < "$LOG_FILE")
    EVENTS=$(grep -c '"type":"event"' "$LOG_FILE" || true)
    DECISIONS=$(grep -c '"type":"decision"' "$LOG_FILE" || true)
    SNAPSHOTS=$(grep -c '"type":"price_snapshot"' "$LOG_FILE" || true)
    POS_STRONG=$(grep -c '"bucket":"POS_STRONG"' "$LOG_FILE" || true)
    echo "  파일: $LOG_FILE"
    echo "  전체: ${TOTAL}줄"
    echo "  이벤트: $EVENTS / 결정: $DECISIONS / 스냅샷: $SNAPSHOTS"
    echo "  POS_STRONG: $POS_STRONG"
else
    echo "  오늘 로그 파일 없음"
fi

echo ""
echo "=== 최근 이벤트 (5건) ==="
if [ -f "$LOG_FILE" ]; then
    grep '"type":"event"' "$LOG_FILE" | tail -5 | python3 -c "
import sys, json
for line in sys.stdin:
    r = json.loads(line)
    src = r.get('source','?')
    ticker = r.get('ticker','')
    bucket = r.get('bucket','')
    skip = r.get('skip_reason','')
    headline = r.get('headline','')[:50]
    print(f'  [{src}] {ticker:6s} {bucket:12s} {skip:20s} {headline}')
" 2>/dev/null || echo "  (파싱 실패)"
fi

echo ""
echo "=== market_ctx (최신) ==="
if [ -f "$LOG_FILE" ]; then
    grep '"market_ctx"' "$LOG_FILE" | tail -1 | python3 -c "
import sys, json
r = json.loads(next(sys.stdin))
m = r.get('market_ctx', {})
kospi = m.get('kospi_change_pct')
kosdaq = m.get('kosdaq_change_pct')
vkospi = m.get('vkospi')
print(f'  KOSPI: {kospi}% / KOSDAQ: {kosdaq}% / VKOSPI: {vkospi}')
" 2>/dev/null || echo "  (데이터 없음)"
fi
