#!/usr/bin/env bash
# verify-live.sh — go-live 후 상태 모니터링 스크립트
#
# 사용법:
#   bash deploy/verify-live.sh          # 로컬에서 SSH로 원격 서버 확인
#   bash deploy/verify-live.sh --local  # 서버에서 직접 실행
#
# 확인 항목:
#   1. 서비스 실행 여부
#   2. 헬스 엔드포인트 응답
#   3. 최근 5분 로그 에러 여부
#   4. 현재 paper/live 모드
#   5. 이벤트 수신 여부 (events_seen)

set -euo pipefail

SERVER_ALIAS="${KS_SSH_ALIAS:-kindshot-server}"
REMOTE_DIR="/opt/kindshot"
SERVICE="kindshot"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[FAIL]${NC} $*"; }

# 원격 실행 래퍼 — --local 플래그 시 ssh 없이 직접 실행
run_remote() {
    if [[ "${LOCAL_MODE:-false}" == "true" ]]; then
        bash -c "$1"
    else
        ssh "$SERVER_ALIAS" "$1"
    fi
}

verify() {
    local overall_ok=true

    echo ""
    echo "━━━ kindshot go-live 후 상태 검증 ━━━"
    echo "  시각: $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo ""

    # ── 1. 서비스 실행 여부 ──
    echo "── 1. 서비스 실행 ──"
    svc_status=$(run_remote "systemctl is-active $SERVICE 2>/dev/null" || echo "unknown")
    if [[ "$svc_status" == "active" ]]; then
        info "서비스: $svc_status"
    else
        error "서비스: $svc_status"
        overall_ok=false
    fi

    # ── 2. 헬스 엔드포인트 ──
    echo ""
    echo "── 2. 헬스 엔드포인트 ──"
    health_json=$(run_remote "curl -s --connect-timeout 3 http://127.0.0.1:8080/health 2>/dev/null" || echo "")
    if [[ -n "$health_json" ]]; then
        # status, mode, daily_pnl, position_count, events_seen 파싱
        parsed=$(echo "$health_json" | python3 -c "
import sys, json
try:
    h = json.loads(sys.stdin.read())
    status  = h.get('status', '?')
    mode    = h.get('mode', '?')
    daily   = h.get('daily_pnl', '?')
    pos_cnt = h.get('position_count', '?')
    events  = h.get('events_seen', '?')
    print(f'status={status} | mode={mode} | daily_pnl={daily} | positions={pos_cnt} | events_seen={events}')
except Exception as e:
    print(f'parse_error: {e}')
" 2>/dev/null || echo "parse_error")
        if echo "$parsed" | grep -q "parse_error"; then
            warn "헬스 응답 파싱 실패: $health_json"
        else
            info "헬스: $parsed"
        fi

        # events_seen이 0이면 경고 (이벤트 미수신 가능성)
        events_seen=$(echo "$health_json" | python3 -c "
import sys, json
h = json.loads(sys.stdin.read())
print(h.get('events_seen', 0))
" 2>/dev/null || echo "0")
        if [[ "$events_seen" == "0" ]]; then
            warn "events_seen=0 — 아직 이벤트 미수신 (장 시작 전이거나 소스 연결 확인 필요)"
        else
            info "이벤트 수신 확인: events_seen=$events_seen"
        fi
    else
        error "헬스 엔드포인트 응답 없음 (http://127.0.0.1:8080/health)"
        overall_ok=false
    fi

    # ── 3. 최근 5분 로그 에러 ──
    echo ""
    echo "── 3. 최근 5분 에러/크리티컬 로그 ──"
    err_lines=$(run_remote "journalctl -u $SERVICE --no-pager --since '5 minutes ago' 2>/dev/null \
        | grep -iE 'ERROR|CRITICAL' || true")
    if [[ -n "$err_lines" ]]; then
        error "에러 감지:"
        echo "$err_lines"
        overall_ok=false
    else
        info "최근 5분 에러 없음"
    fi

    # ── 4. 현재 모드 (paper / live) ──
    echo ""
    echo "── 4. 운영 모드 ──"
    svc_exec=$(run_remote "grep -oP '(?<=ExecStart=).*' /etc/systemd/system/$SERVICE.service 2>/dev/null" || echo "")
    # .env의 KIS_IS_PAPER도 함께 확인
    kis_paper=$(run_remote "cd $REMOTE_DIR && grep -E '^KIS_IS_PAPER=' .env 2>/dev/null | tail -1 | cut -d= -f2" || echo "")
    if echo "$svc_exec" | grep -q -- "--paper"; then
        warn "모드: PAPER (실거래 아님) — systemd에 --paper 플래그 있음"
    elif [[ "$kis_paper" == "true" ]]; then
        warn "모드: PAPER (KIS_IS_PAPER=true) — .env 확인 필요"
    else
        info "모드: LIVE (실거래)"
    fi

    # ── 5. 오늘 JSONL 로그 이벤트 수 ──
    echo ""
    echo "── 5. 오늘 이벤트 로그 ──"
    log_summary=$(run_remote "
LOG_FILE=\"$REMOTE_DIR/logs/kindshot_\$(TZ=Asia/Seoul date +%Y%m%d).jsonl\"
if [ -f \"\$LOG_FILE\" ]; then
    TOTAL=\$(wc -l < \"\$LOG_FILE\")
    EVENTS=\$(grep -c '\"type\":\"event\"' \"\$LOG_FILE\" || true)
    DECISIONS=\$(grep -c '\"type\":\"decision\"' \"\$LOG_FILE\" || true)
    echo \"total=\${TOTAL} events=\${EVENTS} decisions=\${DECISIONS}\"
else
    echo 'no_log_file'
fi
" 2>/dev/null || echo "error")
    if [[ "$log_summary" == "no_log_file" ]]; then
        warn "오늘 JSONL 로그 파일 없음 (장 미개장 또는 경로 확인)"
    elif [[ "$log_summary" == "error" ]]; then
        warn "로그 요약 조회 실패"
    else
        info "로그: $log_summary"
    fi

    # ── 최종 결과 ──
    echo ""
    if [[ "$overall_ok" == "true" ]]; then
        echo -e "${GREEN}━━━ 검증 통과 — 서비스 정상 운영 중 ━━━${NC}"
    else
        echo -e "${RED}━━━ 검증 실패 — 위 항목 확인 필요 ━━━${NC}"
        exit 1
    fi
    echo ""
}

# ── Main ──

if [[ "${1:-}" == "--local" ]]; then
    LOCAL_MODE=true
fi

verify
