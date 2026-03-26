# Session Handoff — 2026-03-26 (7차, 파이어모드)

## 이번 세션 완료 작업

### 긴급 복구 + 전략 개선 2건

| # | 커밋 | 분류 | 내용 |
|---|------|------|------|
| 1 | `872fd8c` | fix | 하락장 confidence 감점 완화 — 최대 -8 → -5 (4단계 세분화) |
| 2 | `5380373` | feat | rule_fallback 고확신 키워드 confidence 상향 + 설계계약 추가 |

### 진단 결과

**3/24~26 거래 0건 원인:**
1. **Anthropic 크레딧 고갈** → LLM circuit breaker 작동
2. **NVIDIA API 키 미설정** (3/26 14:20 이전) → fallback도 실패
3. **14:28 서버 재시작** 후 NVIDIA 키 설정됨 → 14:43부터 NVIDIA LLM 정상 동작
4. **rule_fallback의 BUY도 전멸**: conf 77~80 → market adjustment -8 → 69~72 < min_buy_confidence(75)

**수정 내용:**
1. `apply_market_confidence_adjustment`: -2%이하 일괄 -8 → 4단계(-2/-3/-4/-5) 세분화
   - -0.5~-1%: -2, -1~-2%: -3, -2~-3%: -4, -3%+: -5
   - LLM conf 80 + 폭락장(-3%) = 75 → min_buy_confidence 통과
2. `_HIGH_CONVICTION_KEYWORDS` base confidence 상향:
   - 자사주 소각/FDA/공개매수: 80→82 (폭락장에서도 77 통과)
   - 실적서프라이즈/흑자전환: 78→80
   - 계약/수주/바이오: 78→79
   - 설계계약 키워드 신규 추가

### 분석 결과

**NVIDIA LLM 상태:** 정상 (HTTP 200, Llama-3.1-70B)
**UNKNOWN 리뷰:** 62건 → 2건 프로모션 (conf 90) → main pipeline에서 SKIP 판단 (정상)
**오늘 폭락장:** KOSPI -3.3%, KOSDAQ -3.6% → SKIP 판단 대부분 정확 (false negative 1건, +0.77%)
**3/20 데이터 (LLM 정상 운영):** 7건 BUY 실행, close 기준 대부분 양수 — 시스템 알파 생성 확인

## 현재 상태
- **브랜치:** main
- **테스트:** 625 passed, 0 failed
- **마지막 커밋:** `5380373` feat: rule_fallback 고확신 키워드 confidence 상향
- **서버:** active (running), NVIDIA LLM 정상

## 잔여 기술 부채

### P1 — 긴급
1. **Anthropic 크레딧 충전 또는 제거** — fallback 불가 상태 방치 위험
2. **장전 이벤트 재평가 메커니즘** — iv_ratio=0 이벤트가 DUPLICATE로 영구 소실 (3/20 12건 miss)

### P1 — 전략 검증
3. **SL -0.7% 재검토** — t+5m에서 -1~-3% → close에서 +2~+10% V자 반등 패턴 다수 발견. SL이 잠재적 위너를 조기 손절 가능성
4. **2주 룰 freeze + 데이터 수집** — 새 전략으로 100건+ 거래 필요

### P2 — 기능/전략
5. **Paper → 소액 Live 전환 준비** — KIS live API 키 설정, 주문 실행 모듈
6. **텔레그램 알림 품질 개선** — BUY 시그널에 "왜 BUY인지" 이유 추가
7. **소형주 집중** — ADV 500~2000억 구간 confidence 보너스
8. **확률 기반 진입** — 뉴스 후 2~5분 관찰 후 진입 (구현 복잡)

### P3 — 제품 방향
9. **외부 사용자 확보** — 텔레그램 채널에 지인 1~3명 초대
10. **정보 서비스 pivot 검토** — AI 공시 분석 알림 서비스
