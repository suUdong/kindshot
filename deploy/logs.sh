#!/usr/bin/env bash
set -euo pipefail

# kindshot 로그 실시간 확인 / 조회
# 사용법:
#   bash deploy/logs.sh              # 오늘 로그 tail -f
#   bash deploy/logs.sh journal      # journalctl 실시간
#   bash deploy/logs.sh summary      # 오늘 로그 요약 (log_summary.py)
#   bash deploy/logs.sh report       # 오늘 daily report
#   bash deploy/logs.sh poll         # 오늘 polling trace 최근 20줄
#   bash deploy/logs.sh poll live    # polling trace 실시간
#   bash deploy/logs.sh poll stats   # polling trace 통계 요약
#   bash deploy/logs.sh unknown      # 오늘 UNKNOWN 헤드라인
#   bash deploy/logs.sh 20260310     # 특정 날짜 로그 tail

APP_DIR="/opt/kindshot"
TODAY=$(TZ=Asia/Seoul date +%Y%m%d)
MODE="${1:-}"
SUB="${2:-}"

case "$MODE" in
    journal)
        sudo journalctl -u kindshot -f
        ;;
    summary)
        LOG_FILE="$APP_DIR/logs/kindshot_${SUB:-$TODAY}.jsonl"
        if [ -f "$LOG_FILE" ]; then
            source "$APP_DIR/.venv/bin/activate"
            python "$APP_DIR/deploy/log_summary.py" "$LOG_FILE"
        else
            echo "로그 파일 없음: $LOG_FILE"
        fi
        ;;
    report)
        source "$APP_DIR/.venv/bin/activate"
        python "$APP_DIR/deploy/daily_report.py" "${SUB:-$TODAY}"
        ;;
    poll)
        POLL_FILE="$APP_DIR/logs/polling_trace_${SUB:-$TODAY}.jsonl"
        # sub이 live/stats면 날짜는 오늘
        if [ "$SUB" = "live" ]; then
            POLL_FILE="$APP_DIR/logs/polling_trace_$TODAY.jsonl"
        elif [ "$SUB" = "stats" ]; then
            POLL_FILE="$APP_DIR/logs/polling_trace_$TODAY.jsonl"
        fi

        if [ ! -f "$POLL_FILE" ]; then
            echo "polling trace 없음: $POLL_FILE"
            exit 1
        fi

        case "$SUB" in
            live)
                tail -f "$POLL_FILE" | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        r = json.loads(line.strip())
        phase = r.get('phase','')
        if phase == 'poll_end':
            items = r.get('items', 0)
            raw = r.get('raw', '?')
            dup = r.get('seen_dup', '?')
            err = r.get('error', '')
            lt = r.get('last_time_after', '')
            rmax = r.get('raw_max_time', '')
            ts = r.get('ts', '')[-8:]
            marker = '***' if items > 0 else ''
            print(f'{ts} | items={items} raw={raw} dup={dup} max_t={rmax} last_t={lt} {\"err=\"+err if err else \"\"} {marker}', flush=True)
    except: pass
"
                ;;
            stats)
                python3 -c "
import json, sys
from collections import Counter

total_polls = 0
total_items = 0
total_errors = 0
max_times = []
intervals = []

with open('$POLL_FILE') as f:
    for line in f:
        try:
            r = json.loads(line.strip())
        except: continue
        if r.get('phase') == 'poll_end':
            total_polls += 1
            total_items += r.get('items', 0)
            if r.get('error'): total_errors += 1
            rmax = r.get('raw_max_time', '')
            if rmax: max_times.append(rmax)
        elif r.get('phase') == 'sleep_start':
            intervals.append(r.get('planned_s', 0))

first_ts = last_ts = ''
with open('$POLL_FILE') as f:
    for line in f:
        try:
            r = json.loads(line.strip())
            if not first_ts: first_ts = r.get('ts', '')
            last_ts = r.get('ts', '')
        except: pass

print(f'=== Polling Trace Stats: $POLL_FILE ===')
print(f'기간: {first_ts} ~ {last_ts}')
print(f'총 폴링: {total_polls}회')
print(f'신규 아이템: {total_items}건')
print(f'에러: {total_errors}건')
if max_times:
    print(f'raw_max_time 범위: {min(max_times)} ~ {max(max_times)}')
if intervals:
    avg_int = sum(intervals) / len(intervals)
    print(f'평균 폴링 간격: {avg_int:.1f}초 (min={min(intervals):.1f}, max={max(intervals):.1f})')
"
                ;;
            *)
                # 기본: 최근 20줄 (poll_end만 요약)
                grep '"poll_end"' "$POLL_FILE" | tail -20 | python3 -c "
import sys, json
print('시간       items  raw  dup  max_time  last_time  error')
print('-' * 65)
for line in sys.stdin:
    try:
        r = json.loads(line.strip())
        ts = r.get('ts', '')[-8:]
        items = r.get('items', 0)
        raw = r.get('raw', '?')
        dup = r.get('seen_dup', '?')
        rmax = r.get('raw_max_time', '')
        lt = r.get('last_time_after', '')
        err = r.get('error', '') or ''
        marker = ' <-- NEW' if items > 0 else ''
        print(f'{ts}  {items:>5}  {raw:>3}  {dup:>3}  {rmax:>8}  {lt:>9}  {err}{marker}')
    except: pass
"
                ;;
        esac
        ;;
    unknown)
        DATE_DASH=$(TZ=Asia/Seoul date +%Y-%m-%d)
        if [ -n "$SUB" ]; then
            # 20260312 -> 2026-03-12
            DATE_DASH="${SUB:0:4}-${SUB:4:2}-${SUB:6:2}"
        fi
        bash "$APP_DIR/scripts/review_unknown.sh" "$DATE_DASH"
        ;;
    [0-9]*)
        LOG_FILE="$APP_DIR/logs/kindshot_${MODE}.jsonl"
        if [ -f "$LOG_FILE" ]; then
            tail -20 "$LOG_FILE"
        else
            echo "로그 파일 없음: $LOG_FILE"
        fi
        ;;
    help|--help|-h)
        echo "사용법: bash deploy/logs.sh [명령] [옵션]"
        echo ""
        echo "명령:"
        echo "  (없음)          오늘 로그 tail -f"
        echo "  journal         systemd journalctl 실시간"
        echo "  summary [날짜]  로그 요약 (기본: 오늘)"
        echo "  report [날짜]   daily report (기본: 오늘)"
        echo "  poll            polling trace 최근 20건"
        echo "  poll live       polling trace 실시간 모니터링"
        echo "  poll stats      polling trace 통계 요약"
        echo "  unknown [날짜]  UNKNOWN 버킷 헤드라인"
        echo "  YYYYMMDD        특정 날짜 로그 tail -20"
        echo "  help            이 도움말"
        ;;
    *)
        LOG_FILE="$APP_DIR/logs/kindshot_$TODAY.jsonl"
        if [ -f "$LOG_FILE" ]; then
            tail -f "$LOG_FILE"
        else
            echo "오늘 로그 파일 없음, journalctl로 대체"
            sudo journalctl -u kindshot -f
        fi
        ;;
esac
