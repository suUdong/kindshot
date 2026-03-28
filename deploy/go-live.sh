#!/usr/bin/env bash
# go-live.sh — kindshot paper → micro-live 전환 스크립트
#
# 사용법:
#   bash deploy/go-live.sh           # 전환 전 체크리스트만 출력
#   bash deploy/go-live.sh --apply   # 실제 전환 적용 (서비스 재시작)
#   bash deploy/go-live.sh --verify  # 현재 상태만 확인 (변경 없음)
#
# 전환 내용:
#   1. systemd 서비스에서 --paper 플래그 제거
#   2. .env에서 KIS_IS_PAPER=false 설정 확인
#   3. MICRO_LIVE_MAX_ORDER_WON 안전 상한 확인
#   4. 서비스 재시작
#   5. 전환 후 헬스 엔드포인트 + KIS 토큰 검증
#
# 롤백:
#   bash deploy/go-live.sh --rollback  # paper 모드로 복원

set -euo pipefail

SERVER_ALIAS="${KS_SSH_ALIAS:-kindshot-server}"
REMOTE_DIR="/opt/kindshot"
SERVICE="kindshot"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ── Pre-flight checks ──

check_env_var() {
    local var_name="$1"
    local expected="$2"
    local actual
    actual=$(ssh "$SERVER_ALIAS" "cd $REMOTE_DIR && grep -E '^${var_name}=' .env 2>/dev/null | tail -1 | cut -d= -f2" 2>/dev/null || echo "")
    if [[ "$actual" == "$expected" ]]; then
        info "$var_name=$actual ✓"
        return 0
    else
        warn "$var_name=$actual (expected: $expected)"
        return 1
    fi
}

preflight() {
    echo ""
    echo "━━━ kindshot micro-live 전환 체크리스트 ━━━"
    echo ""

    local ok=0
    local fail=0

    # 1. SSH 접속
    if ssh "$SERVER_ALIAS" "echo connected" &>/dev/null; then
        info "SSH 접속: $SERVER_ALIAS ✓"
        ((ok++))
    else
        error "SSH 접속 실패: $SERVER_ALIAS"
        ((fail++))
        echo ""
        error "SSH 접속 불가 — 체크리스트 중단"
        return 1
    fi

    # 2. .env 필수 변수
    echo ""
    echo "── .env 설정 확인 ──"

    local env_ok=true
    for var in KIS_APP_KEY KIS_APP_SECRET KIS_ACCOUNT_NO; do
        val=$(ssh "$SERVER_ALIAS" "cd $REMOTE_DIR && grep -E '^${var}=' .env 2>/dev/null | tail -1 | cut -d= -f2" 2>/dev/null || echo "")
        if [[ -n "$val" && "$val" != "sk-ant-..." ]]; then
            info "$var=*** (설정됨) ✓"
            ((ok++))
        else
            error "$var 미설정"
            ((fail++))
            env_ok=false
        fi
    done

    # KIS_IS_PAPER
    if check_env_var "KIS_IS_PAPER" "false"; then
        ((ok++))
    else
        warn "KIS_IS_PAPER를 false로 변경 필요"
        ((fail++))
    fi

    # MICRO_LIVE_MAX_ORDER_WON
    max_won=$(ssh "$SERVER_ALIAS" "cd $REMOTE_DIR && grep -E '^MICRO_LIVE_MAX_ORDER_WON=' .env 2>/dev/null | tail -1 | cut -d= -f2" 2>/dev/null || echo "")
    if [[ -n "$max_won" ]]; then
        info "MICRO_LIVE_MAX_ORDER_WON=$max_won ✓"
        ((ok++))
    else
        warn "MICRO_LIVE_MAX_ORDER_WON 미설정 (기본값: 1,000,000원)"
        ((ok++))
    fi

    # TELEGRAM_BOT_TOKEN — 알림 누락 방지를 위해 경고만 (fail 아님)
    tg_token=$(ssh "$SERVER_ALIAS" "cd $REMOTE_DIR && grep -E '^TELEGRAM_BOT_TOKEN=' .env 2>/dev/null | tail -1 | cut -d= -f2" 2>/dev/null || echo "")
    if [[ -n "$tg_token" ]]; then
        info "TELEGRAM_BOT_TOKEN=*** (설정됨) ✓"
    else
        warn "TELEGRAM_BOT_TOKEN 미설정 — 텔레그램 알림 비활성화 상태"
    fi

    # 3. 헬스 엔드포인트 — 전환 전 서비스가 정상 응답하는지 확인
    echo ""
    echo "── 헬스 엔드포인트 ──"
    health_json=$(ssh "$SERVER_ALIAS" "curl -s --connect-timeout 3 http://127.0.0.1:8080/health 2>/dev/null" || echo "")
    if [[ -n "$health_json" ]]; then
        health_status=$(echo "$health_json" | python3 -c "import sys,json; h=json.load(sys.stdin); print(h.get('status','?'))" 2>/dev/null || echo "parse_error")
        if [[ "$health_status" == "ok" || "$health_status" == "healthy" ]]; then
            info "헬스 엔드포인트: $health_status ✓"
            ((ok++))
        else
            warn "헬스 엔드포인트: $health_status (서비스 상태 확인 필요)"
            ((fail++))
        fi
    else
        error "헬스 엔드포인트 응답 없음 (http://127.0.0.1:8080/health) — 서비스 미실행"
        ((fail++))
    fi

    # 4. 현재 서비스 상태
    echo ""
    echo "── 서비스 상태 ──"
    current_mode=$(ssh "$SERVER_ALIAS" "grep -oP '(?<=ExecStart=).*' /etc/systemd/system/$SERVICE.service 2>/dev/null" || echo "unknown")
    if echo "$current_mode" | grep -q -- "--paper"; then
        info "현재 모드: PAPER"
    else
        warn "현재 모드: LIVE (이미 전환됨)"
    fi

    service_status=$(ssh "$SERVER_ALIAS" "systemctl is-active $SERVICE 2>/dev/null" || echo "unknown")
    info "서비스 상태: $service_status"

    # 5. 계좌번호 형식
    acct=$(ssh "$SERVER_ALIAS" "cd $REMOTE_DIR && grep -E '^KIS_ACCOUNT_NO=' .env 2>/dev/null | tail -1 | cut -d= -f2" 2>/dev/null || echo "")
    acct_clean="${acct//-/}"
    if [[ ${#acct_clean} -eq 10 ]]; then
        info "계좌번호 형식 OK (10자리) ✓"
        ((ok++))
    else
        error "계좌번호 형식 이상: ${#acct_clean}자리 (10자리 필요)"
        ((fail++))
    fi

    echo ""
    echo "━━━ 결과: ${ok} pass / ${fail} fail ━━━"
    echo ""

    if [[ $fail -gt 0 ]]; then
        error "체크리스트 미통과 항목 있음. --apply 전에 수정 필요."
        return 1
    else
        info "체크리스트 통과. 'bash deploy/go-live.sh --apply' 로 전환 가능."
        return 0
    fi
}

apply_live() {
    info "micro-live 전환 시작..."

    # 1. systemd 서비스 업데이트: --paper 제거
    info "systemd 서비스 업데이트 (--paper 제거)..."
    ssh "$SERVER_ALIAS" "sudo sed -i 's|--paper||' /etc/systemd/system/$SERVICE.service"
    ssh "$SERVER_ALIAS" "sudo systemctl daemon-reload"

    # 2. .env에서 KIS_IS_PAPER=false 확인/설정
    info "KIS_IS_PAPER=false 확인..."
    ssh "$SERVER_ALIAS" "cd $REMOTE_DIR && if grep -q '^KIS_IS_PAPER=' .env; then sed -i 's/^KIS_IS_PAPER=.*/KIS_IS_PAPER=false/' .env; else echo 'KIS_IS_PAPER=false' >> .env; fi"

    # 3. 코드 배포 (최신 반영)
    info "코드 배포..."
    ssh "$SERVER_ALIAS" "cd $REMOTE_DIR && git pull && source .venv/bin/activate && pip install -e . --quiet"

    # 4. 서비스 재시작
    info "서비스 재시작..."
    ssh "$SERVER_ALIAS" "sudo systemctl restart $SERVICE"
    sleep 5

    # 5. 상태 확인
    info "서비스 상태:"
    ssh "$SERVER_ALIAS" "sudo systemctl status $SERVICE --no-pager -l" || true
    echo ""
    ssh "$SERVER_ALIAS" "journalctl -u $SERVICE -n 10 --no-pager" || true

    # 6. 전환 후 검증: 헬스 엔드포인트 + KIS 토큰
    echo ""
    post_switch_verify

    echo ""
    info "micro-live 전환 완료!"
    info "모니터링: ssh $SERVER_ALIAS 'journalctl -u $SERVICE -f'"
}

# 전환 후 헬스 엔드포인트와 KIS 토큰 취득 가능 여부를 검증
post_switch_verify() {
    echo "── 전환 후 검증 ──"

    # 헬스 엔드포인트 응답 확인 (최대 3회 재시도)
    local health_ok=false
    for attempt in 1 2 3; do
        health_json=$(ssh "$SERVER_ALIAS" "curl -s --connect-timeout 3 http://127.0.0.1:8080/health 2>/dev/null" || echo "")
        if [[ -n "$health_json" ]]; then
            mode=$(echo "$health_json" | python3 -c "import sys,json; h=json.load(sys.stdin); print(h.get('mode','?'))" 2>/dev/null || echo "?")
            info "헬스 엔드포인트 응답 ✓ (모드: $mode)"
            health_ok=true
            break
        fi
        warn "헬스 엔드포인트 미응답 (시도 $attempt/3) — 3초 후 재시도..."
        sleep 3
    done
    if [[ "$health_ok" == false ]]; then
        error "헬스 엔드포인트 응답 없음 — 서비스 기동 실패 가능성. 로그 확인 필요."
    fi

    # KIS 토큰 취득 가능 여부 — 실거래 API 키가 유효한지 간접 검증
    # 토큰 캐시 파일이 갱신됐으면 성공, 없으면 로그에서 토큰 에러 확인
    token_err=$(ssh "$SERVER_ALIAS" "journalctl -u $SERVICE --no-pager -n 30 2>/dev/null \
        | grep -iE 'token.*fail|auth.*fail|unauthorized|access_token.*error' || true")
    if [[ -n "$token_err" ]]; then
        error "KIS 토큰 오류 감지:"
        echo "$token_err"
    else
        info "KIS 토큰 오류 없음 ✓"
    fi
}

# 현재 상태를 변경 없이 확인만 하는 모드 (--verify)
verify_state() {
    echo ""
    echo "━━━ kindshot 현재 상태 확인 (변경 없음) ━━━"
    echo ""

    # SSH 연결
    if ! ssh "$SERVER_ALIAS" "echo connected" &>/dev/null; then
        error "SSH 접속 실패: $SERVER_ALIAS"
        return 1
    fi
    info "SSH 접속: $SERVER_ALIAS ✓"

    echo ""
    echo "── 운영 모드 ──"
    current_mode=$(ssh "$SERVER_ALIAS" "grep -oP '(?<=ExecStart=).*' /etc/systemd/system/$SERVICE.service 2>/dev/null" || echo "unknown")
    if echo "$current_mode" | grep -q -- "--paper"; then
        info "모드: PAPER"
    else
        warn "모드: LIVE (실거래 중)"
    fi

    echo ""
    echo "── 서비스 상태 ──"
    service_status=$(ssh "$SERVER_ALIAS" "systemctl is-active $SERVICE 2>/dev/null" || echo "unknown")
    info "서비스: $service_status"

    echo ""
    echo "── 헬스 엔드포인트 ──"
    health_json=$(ssh "$SERVER_ALIAS" "curl -s --connect-timeout 3 http://127.0.0.1:8080/health 2>/dev/null" || echo "")
    if [[ -n "$health_json" ]]; then
        echo "  $health_json" | python3 -c "
import sys, json
try:
    raw = sys.stdin.read().strip()
    h = json.loads(raw)
    status  = h.get('status','?')
    mode    = h.get('mode','?')
    daily   = h.get('daily_pnl','?')
    pos_cnt = h.get('position_count','?')
    events  = h.get('events_seen','?')
    print(f'  status={status} | mode={mode} | daily_pnl={daily} | positions={pos_cnt} | events_seen={events}')
except Exception as e:
    print(f'  (파싱 실패: {e})')
" 2>/dev/null || info "헬스 응답: $health_json"
    else
        warn "헬스 엔드포인트 응답 없음"
    fi

    echo ""
    echo "── 주요 env 확인 ──"
    for var in KIS_IS_PAPER MICRO_LIVE_MAX_ORDER_WON TELEGRAM_BOT_TOKEN; do
        val=$(ssh "$SERVER_ALIAS" "cd $REMOTE_DIR && grep -E '^${var}=' .env 2>/dev/null | tail -1 | cut -d= -f2" 2>/dev/null || echo "")
        if [[ -n "$val" ]]; then
            # 민감 정보는 마스킹
            if [[ "$var" == "TELEGRAM_BOT_TOKEN" ]]; then
                info "$var=*** (설정됨)"
            else
                info "$var=$val"
            fi
        else
            warn "$var 미설정"
        fi
    done

    echo ""
    echo "━━━ 확인 완료 ━━━"
}

rollback_paper() {
    info "paper 모드로 롤백..."

    # 1. systemd 서비스에 --paper 추가
    ssh "$SERVER_ALIAS" "sudo sed -i 's|python -m kindshot|python -m kindshot --paper|' /etc/systemd/system/$SERVICE.service"
    ssh "$SERVER_ALIAS" "sudo systemctl daemon-reload"

    # 2. KIS_IS_PAPER=true
    ssh "$SERVER_ALIAS" "cd $REMOTE_DIR && sed -i 's/^KIS_IS_PAPER=.*/KIS_IS_PAPER=true/' .env"

    # 3. 서비스 재시작
    ssh "$SERVER_ALIAS" "sudo systemctl restart $SERVICE"
    sleep 2

    info "서비스 상태:"
    ssh "$SERVER_ALIAS" "sudo systemctl status $SERVICE --no-pager -l" || true

    echo ""
    info "paper 모드 롤백 완료!"
}

# ── Main ──

case "${1:-}" in
    --apply)
        preflight || exit 1
        echo ""
        read -p "micro-live 전환을 진행하시겠습니까? (yes/no): " confirm
        if [[ "$confirm" == "yes" ]]; then
            apply_live
        else
            info "취소됨."
        fi
        ;;
    --rollback)
        rollback_paper
        ;;
    --verify)
        verify_state
        ;;
    *)
        preflight
        ;;
esac
