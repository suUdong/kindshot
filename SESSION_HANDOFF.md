# Session Handoff — 2026-03-26 (9차, 파이어모드)

## 이번 세션 완료 작업

### 수익성 개선 v5 — SKIP 편향 해소

| # | 커밋 | 분류 | 내용 |
|---|------|------|------|
| 1 | `da51250` | feat | SKIP 편향 해소 — 프롬프트 리밸런싱 + rule_fallback 키워드 확대 + circuit breaker 강화 |

### 변경 상세

**핵심 진단: BUY 시그널 0건 (3/24~3/26)**
- Anthropic 크레딧 소진 → LLM fallback 불가
- NVIDIA LLM 작동하지만 conf=50 반환 (극단적 SKIP 편향 프롬프트)
- Rule fallback conf=72 → min_buy_confidence=75 미달 → 전부 SKIP
- 결과: 시스템이 며칠째 사실상 정지

**1. LLM 프롬프트 리밸런싱 (`decision_strategy.txt`)**
- "72는 죽음의 숫자", "시스템 장애" 등 공포 언어 제거
- LOSS 10개 vs WIN 6개 불균형 → WIN 5 / LOSS 6 균형
- 소형 확정 수주(100~500억) + 소/중형주 → BUY(75,S) 경로 추가
- decision_bias: "의심→SKIP" → "조건 충족→적극 BUY" 전환

**2. Rule fallback 키워드 확대 (`decision.py`)**
- `_HIGH_CONVICTION_KEYWORDS`에 15개+ 추가:
  - 단일판매/규모공급계약 (KIND 정규공시), 해외수주, 방산수주, 독점공급, 장기공급
  - 기술수출/라이선스아웃, 임상2상 완료/성공, 특허 등록/취득/확보
  - 인수 완료/결정, 역대 최대 실적
- POS_STRONG 최소 confidence: 77→76

**3. Circuit breaker 쿨다운 강화 (`llm_client.py`)**
- 영구 에러(크레딧 부족 등) 쿨다운: 5분→1시간
- 불필요한 Anthropic API 재시도 대폭 감소

**4. 텔레그램 BUY 알림 개선 (`telegram_ops.py`)**
- BUY 이유(`reason`) ">> " 강조 표시
- 헤드라인 표시: 60→120자 확대
- bucket 표시 제거, kw 표시 추가

## 현재 상태
- **브랜치:** main
- **테스트:** 634 passed, 0 failed
- **마지막 커밋:** `da51250` feat: SKIP 편향 해소
- **서버:** active (running), 16:28 재시작 완료

## 잔여 기술 부채

### P1 — 긴급
1. **Anthropic 크레딧 충전 또는 제거** — fallback 불가 상태 (circuit breaker 1시간으로 완화)
2. **3/27 장중 모니터링** — BUY 시그널 발생 여부 확인 (프롬프트 변경 효과 검증)

### P1 — 전략 검증
3. ~~SL -0.7% 재검토~~ → **완료** (v4에서 -1.5%로 완화)
4. ~~장전 이벤트 재평가~~ → **완료** (pending 큐 + 09:01 재주입)
5. ~~SKIP 편향 해소~~ → **완료** (v5 프롬프트 리밸런싱)
6. **2주 룰 freeze + 데이터 수집** — 새 전략으로 100건+ 거래 필요

### P2 — 기능/전략
7. **Paper → 소액 Live 전환 준비** — KIS live API 키 설정, 주문 실행 모듈
8. ~~텔레그램 알림 품질 개선~~ → **완료** (BUY 이유 강조)
9. ~~소형주 집중~~ → **완료** (ADV 500~2000억 +3 보너스)
10. **확률 기반 진입** — 뉴스 후 2~5분 관찰 후 진입 (구현 복잡)

### P3 — 제품 방향
11. **외부 사용자 확보** — 텔레그램 채널에 지인 1~3명 초대
12. **정보 서비스 pivot 검토** — AI 공시 분석 알림 서비스
