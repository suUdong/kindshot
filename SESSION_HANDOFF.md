# Session Handoff — 2026-03-26 (10차, 파이어모드)

## 이번 세션 완료 작업

### 수익성 개선 v7 — Hold Profile 연동 TP/SL + Stale Position Exit

| # | 커밋 | 분류 | 내용 |
|---|------|------|------|
| 1 | (pending) | feat | hold profile 연동 TP/SL + stale position exit |

### 변경 상세

**핵심 진단: 승률 25.8%, 누적 -24.31%의 원인**
- TP/SL이 confidence만 반영, 촉매 유형(hold profile)을 무시
- 수주(빠른 반전)와 자사주소각(장기 트렌드)에 동일한 TP/SL 적용
- 모멘텀 소멸 포지션에 대한 탈출 전략 부재

**1. Hold Profile 연동 TP/SL (`guardrails.py`)**
- `get_dynamic_tp_pct()`, `get_dynamic_stop_loss_pct()`에 `hold_minutes` 파라미터 추가
- EOD hold (자사주소각, 공개매수): TP ×1.5, SL ×1.3 — 장기 트렌드 수익 극대화
- 수주/공급계약 (hold≤15분): TP ×0.7 — 빠른 반전 전 이익 확보
- 표준 (특허/임상, hold>15분): 기존 confidence 기반 유지

**2. Stale Position Exit (`price.py`)**
- 진입 후 5분 경과 + 수익률 ±0.2% 미만 → 모멘텀 소멸 판단, 자동 탈출
- EOD hold (hold_minutes=0)는 stale 판정 제외 (장기 촉매)
- 불필요한 보유 시간 감소 → 기회비용 절감

**3. Price Tracker hold_minutes 연동 (`price.py`)**
- TP/SL 계산 시 이벤트별 hold_minutes를 guardrails 함수에 전달
- 촉매 유형별 맞춤형 출구 전략 완성

## 이전 세션 완료 작업

### 수익성 개선 v6 — 하락장 고확신 촉매 바이패스 + hold_profile 확대 + SKIP 알림
- `285c95a` feat: 하락장에서도 고확신 촉매(conf>=82) LLM 판단 허용
- 텔레그램 high-conf SKIP 알림 추가 (false negative 모니터링)

### 수익성 개선 v5 — SKIP 편향 해소
- `da51250` feat: 프롬프트 리밸런싱 + rule_fallback 키워드 확대 + circuit breaker 강화

## 현재 상태
- **브랜치:** main
- **테스트:** 637 passed, 0 failed (+3 신규 테스트)
- **서버:** active (running) — 배포 필요

## 잔여 기술 부채

### P1 — 긴급
1. **Anthropic 크레딧 충전 또는 제거** — fallback 불가 상태
2. **3/27 장중 모니터링** — BUY 시그널 발생 + hold profile TP/SL 효과 확인
3. **2주 룰 freeze + 데이터 수집** — 새 전략으로 100건+ 거래 필요

### P2 — 기능/전략
4. **Paper → 소액 Live 전환 준비** — KIS live API 키 설정, 주문 실행 모듈
5. **확률 기반 진입** — 뉴스 후 2~5분 관찰 후 진입 (구현 복잡)
6. **Volume 확인 게이트** — 진입 시 거래량 급증 확인

### P3 — 제품 방향
7. **외부 사용자 확보** — 텔레그램 채널에 지인 1~3명 초대
8. **정보 서비스 pivot 검토** — AI 공시 분석 알림 서비스
