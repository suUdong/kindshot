# 월요일 장 시작 전 실거래 체크리스트

> 작성일: 2026-03-28 (토)
> 대상: 2026-03-30 (월) 장 시작 전 점검

---

## 1. 최근 배포 변경사항 (v78~v80)

### v80 — _sell_triggered 영구 차단 방지 (4dfd2c9, 7d83ab1)
- **문제**: trade close 콜백 실패 시 `_sell_triggered`가 영구적으로 True로 남아 해당 포지션의 매도가 차단됨
- **수정**: 콜백 에러 방어 + `_sell_triggered` 리셋 로직 추가
- **검증**: 콜백 실패 시에도 매도가 재시도되는지 로그에서 확인

### v79 — Y2iFeed 유튜브 인사이트 시그널 연동 (472db9c)
- **내용**: y2i 프로젝트의 signal_tracker.json을 읽어 유튜브 기반 투자 시그널을 파이프라인에 공급
- **설정 필요**: `Y2I_FEED_ENABLED=true`, `Y2I_SIGNAL_PATH` (서버에 y2i 데이터 경로)
- **현재 상태**: ⚠️ 서버 .env에 Y2I 관련 변수 미설정 → 비활성 상태 (기본값 `false`)
- **조치**: 월요일 바로 활성화할 필요 없음. Y2I 데이터가 서버에 준비된 후 활성화

### v78 — 가드레일 과잉 차단 완화 (f5177a2)
- **문제**: 84% 차단률 — 73건 분석 결과 과도한 가드레일 규칙이 유효한 BUY를 차단
- **수정**: 가드레일 임계값 완화, 불필요한 차단 규칙 제거
- **검증**: 월요일 장중 차단률이 40~60% 수준으로 정상화되는지 모니터링

### alpha_scanner 연동
- **내용**: 외부 alpha-scanner 서비스의 종목 conviction을 의사결정 컨텍스트에 반영
- **현재 상태**: ⚠️ 서버 .env에 `ALPHA_SCANNER_API_BASE_URL` 미설정 → 비활성
- **조치**: alpha-scanner 서비스 URL 확보 후 설정

---

## 2. 서버 프로세스 상태 확인

```bash
# kindshot 메인 프로세스
ssh kindshot-server "sudo systemctl status kindshot --no-pager"
# 기대: Active: active (running), --paper 모드

# 대시보드
ssh kindshot-server "sudo systemctl status kindshot-dashboard --no-pager"
# 기대: Active: active (running), port 8501

# 최근 로그 (에러 없는지 확인)
ssh kindshot-server "journalctl -u kindshot -n 50 --no-pager | grep -E '(ERROR|CRITICAL|Exception)'"
# 기대: 출력 없음

# Health endpoint
ssh kindshot-server "curl -s http://127.0.0.1:8080/health | python3 -m json.tool | head -20"
# 기대: status=healthy, circuit_breaker 모두 false
```

### 2026-03-28 진단 결과
| 항목 | 상태 | 비고 |
|------|------|------|
| kindshot.service | ✅ active (running) | paper 모드, PID 153070 |
| kindshot-dashboard.service | ✅ active (running) | port 8501, 12시간 가동 |
| Health endpoint | ✅ healthy | circuit breaker 정상 |
| Heartbeat | ✅ 30초 간격 정상 | events_seen=0 (장 마감 후) |
| Market monitor | ✅ 초기화 완료 | KOSPI -0.40% |
| 에러/Exception | ✅ 없음 | 클린 로그 |

---

## 3. KIS API 인증 상태 확인

### 현재 서버 환경변수
| 변수 | 상태 |
|------|------|
| KIS_APP_KEY | ✅ 설정됨 |
| KIS_APP_SECRET | ✅ 설정됨 |
| KIS_ACCOUNT_NO | ✅ 설정됨 |
| KIS_IS_PAPER | ⚠️ `true` (paper 모드) |

### 실거래 전환 절차
```bash
# 1. 서버 .env 수정
ssh kindshot-server "sudo nano /opt/kindshot/.env"
# KIS_IS_PAPER=false 로 변경
# (실거래용 APP_KEY/SECRET이 paper용과 다르다면 함께 변경)

# 2. 서비스 재시작
ssh kindshot-server "sudo systemctl restart kindshot"

# 3. 전환 확인
ssh kindshot-server "journalctl -u kindshot -n 10 --no-pager"
# 기대: "--paper" 플래그 없이 시작됨
```

### ⚠️ 실거래 전환 전 주의사항
1. **실거래용 KIS 앱키**가 paper 앱키와 다를 수 있음 — 한국투자증권 개발자센터에서 확인
2. **KIS_ACCOUNT_NO**가 실계좌 번호인지 확인 (모의투자 계좌번호 ≠ 실계좌)
3. **토큰 발급 확인**: 재시작 후 로그에서 `KIS token` 관련 메시지 확인
4. **듀얼 서버**: `kis_client.py`가 실전 시세 + 모의 주문 듀얼 서버를 지원하나, 실거래 전환 시 주문도 실전 서버로 전환됨

### KIS API 연결 테스트
```bash
# 재시작 후 토큰 발급 확인
ssh kindshot-server "journalctl -u kindshot -n 20 --no-pager | grep -i 'token\|KIS\|auth'"

# Health에서 KIS 에러 확인
ssh kindshot-server "curl -s http://127.0.0.1:8080/health | python3 -c 'import json,sys; d=json.load(sys.stdin); print(f\"kis_calls={d[\"kis_calls\"]}, kis_errors={d[\"kis_errors\"]}\")'"
```

---

## 4. 대시보드 정상 확인

```bash
# SSH 터널로 대시보드 접근
ssh -L 8501:localhost:8501 kindshot-server
# 브라우저에서 http://localhost:8501 접속
```

### 확인 항목
- [ ] 메인 페이지 로딩 정상
- [ ] 누적 PnL 곡선 표시
- [ ] 최근 거래 이력 표시
- [ ] 전략 분석 탭 동작
- [ ] 자동 새로고침 동작

### 2026-03-28 진단 결과
- 서비스 가동 중 (12시간+), Streamlit 포트 8501 정상 바인딩

---

## 5. 누락된 환경변수 (선택 사항)

아래 변수들은 현재 서버에 미설정이나, 핵심 기능에는 영향 없음:

| 변수 | 용도 | 영향 | 우선순위 |
|------|------|------|----------|
| TELEGRAM_BOT_TOKEN | 텔레그램 BUY/매매 알림 | 알림 미발송 | 높음 (모니터링) |
| TELEGRAM_CHAT_ID | 텔레그램 채팅방 ID | 알림 미발송 | 높음 (모니터링) |
| NVIDIA_API_KEY | NVIDIA LLM (llama-3.3-70b) | Anthropic으로 fallback 중 | 중간 |
| Y2I_FEED_ENABLED | 유튜브 인사이트 시그널 | 피드 비활성 | 낮음 |
| ALPHA_SCANNER_API_BASE_URL | 알파 스캐너 conviction | 기능 비활성 | 낮음 |

### 텔레그램 설정 (권장)
```bash
# 서버 .env에 추가
TELEGRAM_BOT_TOKEN=<봇 토큰>
TELEGRAM_CHAT_ID=<채팅방 ID>
```

---

## 6. 실거래 전환 최종 체크리스트

### 장 시작 전 (08:30 이전)
- [ ] 서버 SSH 접속 확인: `ssh kindshot-server`
- [ ] `kindshot.service` 정상 구동 확인
- [ ] `kindshot-dashboard.service` 정상 구동 확인
- [ ] Health endpoint 정상 (`curl http://127.0.0.1:8080/health`)
- [ ] 최근 로그에 ERROR/CRITICAL 없음
- [ ] KIS 토큰 발급 정상
- [ ] (선택) 텔레그램 알림 설정 완료

### 실거래 전환 시
- [ ] `.env`에서 `KIS_IS_PAPER=false` 변경
- [ ] 실거래용 APP_KEY/SECRET/ACCOUNT_NO 확인
- [ ] `sudo systemctl restart kindshot`
- [ ] 재시작 후 로그에서 paper 플래그 없음 확인
- [ ] Health endpoint에서 `circuit_breaker` 정상 확인
- [ ] `guardrail_state.configured_max_positions` 확인 (현재: 4)

### 장 시작 후 (09:00~09:30) 모니터링
- [ ] 첫 뉴스 이벤트 수신 확인 (Heartbeat events_seen > 0)
- [ ] 가드레일 차단률 모니터링 — v78 완화 후 40~60% 목표
- [ ] BUY 시그널 발생 시 주문 실행 확인
- [ ] (v80) 매도 콜백 실패 시 _sell_triggered 리셋 확인
- [ ] 대시보드에서 실시간 거래 반영 확인

### 비상 롤백
```bash
# 즉시 paper 모드로 복귀
ssh kindshot-server "sudo sed -i 's/KIS_IS_PAPER=false/KIS_IS_PAPER=true/' /opt/kindshot/.env && sudo systemctl restart kindshot"

# 서비스 중지 (긴급)
ssh kindshot-server "sudo systemctl stop kindshot"
```

---

## 7. 코드 동기화 상태

| 항목 | 상태 |
|------|------|
| 로컬 ↔ 서버 소스 파일 | ✅ 동일 (44개 .py 파일) |
| 서버 배포 방식 | rsync (git 없음) |
| 최신 배포 시간 | 2026-03-28 22:05 (config.py 기준) |
| LLM_MODEL (서버) | claude-haiku-4-5-20251001 |
| LLM_PROVIDER 기본값 | nvidia (NVIDIA_API_KEY 미설정 → Anthropic fallback) |
| FEED_SOURCE (서버) | KIS |
