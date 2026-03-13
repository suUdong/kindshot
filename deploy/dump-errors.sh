#!/usr/bin/env bash
# 서버 에러 로그를 파일로 덤프
# 사용법: bash deploy/dump-errors.sh [YYYYMMDD]

set -euo pipefail

APP_DIR="/opt/kindshot"
TODAY=$(TZ=Asia/Seoul date +%Y%m%d)
DATE="${1:-$TODAY}"

OUT="$APP_DIR/logs/error_dump_${DATE}.txt"

echo "=== Error dump for $DATE ===" > "$OUT"
echo "Generated at: $(TZ=Asia/Seoul date)" >> "$OUT"
echo "" >> "$OUT"

echo "=== journalctl errors ===" >> "$OUT"
sudo journalctl -u kindshot --since "${DATE:0:4}-${DATE:4:2}-${DATE:6:2} 00:00" \
     --until "${DATE:0:4}-${DATE:4:2}-${DATE:6:2} 23:59" \
     --no-pager 2>/dev/null | grep -i "error\|traceback\|exception\|critical" >> "$OUT" 2>/dev/null || echo "(none)" >> "$OUT"

echo "" >> "$OUT"
echo "=== journalctl last 100 lines ===" >> "$OUT"
sudo journalctl -u kindshot --since "${DATE:0:4}-${DATE:4:2}-${DATE:6:2} 00:00" \
     --until "${DATE:0:4}-${DATE:4:2}-${DATE:6:2} 23:59" \
     --no-pager -n 100 >> "$OUT" 2>/dev/null || echo "(none)" >> "$OUT"

echo "" >> "$OUT"
echo "=== LLM-related log entries ===" >> "$OUT"
grep -i "llm\|anthropic\|decision\|claude" "$APP_DIR/logs/kindshot_${DATE}.jsonl" >> "$OUT" 2>/dev/null || echo "(none)" >> "$OUT"

echo "Saved to $OUT"
