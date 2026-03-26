# Session Handoff — 2026-03-26 (15차, 파이어모드)

## 이번 세션 완료 작업 (v44~v56, 13개 개선)

| # | 커밋 | 내용 | 영향 |
|---|------|------|------|
| 1 | `d75de61` | **v44: hold_profile 키워드 대폭 보강** | 자사주매입/자기주식 EOD hold 정상화 |
| 2 | `0e2a1c8` | **v45: dorg 기반 뉴스/공시 구분** ★ | 뉴스 출처 -5 감점, false positive ↓ |
| 3 | `3146d6a` | **v46: 시간대별 confidence 조정** ★★ | 장전 공시 +5, 비유동 시간대 -3 |
| 4 | `a7437b6` | v47: 09:00~09:30 size_hint 캡 (L→M) | 장 초반 변동성 리스크 ↓ |
| 5 | `1c25ad7` | v48: spread>30bps size 다운 | 유동성 리스크 반영 |
| 6 | `1e615c7` | **v49: 상승장 confidence +3 부스트** ★ | breadth_ratio 연동 |
| 7 | `da483f8` | **v50: 소규모 계약(<100억) preflight SKIP** ★ | LLM 호출 절감 + false positive ↓ |
| 8 | `a7b97f6` | v51: NEG_STRONG 키워드 35개 추가 | 분류 정확도 ↑ |
| 9 | `38f3650` | **v52: P&L 계산 size_hint 반영 (버그)** ★★★ | daily_loss_limit 정확도 복구 |
| 10 | `5f5b548` | v53: stale exit threshold 동적화 | 고확신 포지션 조기 exit 방지 |
| 11 | `453b211` | v54: IGNORE_OVERRIDE 기사 패턴 보강 | LLM 호출 절감 |
| 12 | `785d3f2` | **v55: confidence cap + liquidity fix** ★★ | BUY 유지 + S-size false reject 방지 |
| 13 | `43e6b17` | v56: 장전 participation check 비활성 | 장전 공시 false reject 방지 |

### 핵심 발견 + 해결 (임팩트 순)

**1. P&L 계산 M-size 고정 버그 ★★★ (v52)**
P&L callback이 config.order_size(M=500만원)으로 고정 → S/L 포지션 P&L 부정확.
Fix: event별 actual order_size 추적, size_hint 기반 정확한 P&L 계산.

**2. 프롬프트 전략 미구현 ★★ (v46~v50, v56)**
decision_strategy.txt에 명시된 규칙 6개가 코드에 미구현:
- 장전 공시 +5 부스트 / 비유동 -3 (v46)
- 장 초반 size 캡 / spread>30 size 다운 (v47, v48)
- 상승장 +3 부스트 / 소규모 계약 SKIP (v49, v50)
- 장전 participation check 비활성 (v56)

**3. confidence cap + liquidity fix ★★ (v55)**
감점 후 min_buy_confidence(75) 이하 드롭 방지 + S-size 포지션 false reject 수정.

**4. hold_profile / dorg / 키워드 갭 ★ (v44, v45, v51, v54)**
자사주 매입/자기주식 소각·취득/CDMO/기술이전 hold_profile 누락 수정.
dorg 기반 뉴스 필터 + NEG 35개 + IGNORE 31개 키워드 보강.

## 현재 상태
- **브랜치:** main, v44~v56 pushed
- **테스트:** 684 passed (+24 신규, 660→684)
- **서버:** active (running), v56 최종 배포

## 다음 세션 우선순위

### P0 — 즉시
1. **서버 .env에 KIS 실전 API 키 추가** (14차에서 이월)
2. **3/27 장중 모니터링** — v44~v56 효과 검증

### P1 — 데이터 수집
3. **2주 룰 freeze** — 실시간 시세 기반 100건+ 거래 데이터
4. **dorg 필드 분석 심화** — 뉴스 출처별 승률 통계

### P2 — 기능
5. **Paper → 소액 Live 전환**
6. **확률 기반 진입** — 뉴스 후 2~5분 관찰
7. **탐색 에이전트 잔여 항목:**
   - prior_volume_rate ContextCard 필드 추가 (#5)
   - breadth_ratio 보수적 선택 검증 (#6)

### P3 — 제품
8. **텔레그램 채널 지인 초대**
9. **AI 공시 분석 서비스 pivot**
