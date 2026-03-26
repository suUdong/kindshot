# Session Handoff — 2026-03-26 (15차, 파이어모드)

## 이번 세션 완료 작업 (v44~v54, 11개 개선)

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

### 핵심 발견 + 해결 (임팩트 순)

**1. P&L 계산 M-size 고정 버그 ★★★ (v52)**
P&L callback이 config.order_size(M=500만원)으로 고정 → S/L 포지션 P&L 부정확 → daily_loss_limit 판단 오류.
Fix: event별 actual order_size 추적, size_hint 기반 정확한 P&L 계산.

**2. 프롬프트 전략 미구현 ★★ (v46, v47, v48, v49, v50)**
decision_strategy.txt에 명시된 규칙 5개가 코드에 미구현:
- 장전 공시 +5 부스트 (v46)
- 비유동 시간대 -3 (v46)
- 장 초반 size 캡 (v47)
- spread>30 size 다운 (v48)
- 상승장 +3 부스트 (v49)
- 소규모 계약 SKIP (v50)

**3. hold_profile 키워드 갭 ★ (v44)**
자사주 매입/자기주식 소각·취득/CDMO/기술이전 키워드가 hold_profile에 누락.
→ 20분 기본값 적용 (EOD hold가 정답). TP/SL 부정확.

**4. dorg 기반 뉴스 필터 ★ (v45)**
KIND/거래소 공시 vs 뉴스 기사 구분으로 confidence 차등화. 뉴스 출처 → -5.

## 현재 상태
- **브랜치:** main, v44~v54 pushed
- **테스트:** 684 passed (+24 신규, 660→684)
- **서버:** 배포 필요 (아래 참조)

## 다음 세션 우선순위

### P0 — 즉시
1. **서버 배포** — v44~v54 반영
2. **서버 .env에 KIS 실전 API 키 추가** (14차 handoff에서 이월)
3. **3/27 장중 모니터링** — 모든 개선 효과 검증

### P1 — 데이터 수집
4. **2주 룰 freeze** — 실시간 시세 기반 100건+ 거래 데이터
5. **dorg 필드 분석 심화** — 뉴스 출처별 승률 통계

### P2 — 기능
6. **Paper → 소액 Live 전환**
7. **확률 기반 진입** — 뉴스 후 2~5분 관찰
8. **explore agent 발견 추가 구현:**
   - breadth_ratio 선택 로직 검증 (min vs max)
   - confidence adj cap 로직 개선 (min_buy_confidence 보장)
   - prior_volume_rate ContextCard 필드 추가

### P3 — 제품
9. **텔레그램 채널 지인 초대**
10. **AI 공시 분석 서비스 pivot**
