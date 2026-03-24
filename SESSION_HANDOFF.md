# Session Handoff — 2026-03-24 (6차, 야간 자율 운영)

## 이번 세션 완료 작업

### 전략 개선 7건 — fire-profitability 기반

| # | 커밋 | 분류 | 내용 |
|---|------|------|------|
| 1 | `41fc426` | 전략 | 시간 기반 trailing stop (early 0.3%, mid 0.5%, late 0.7%) + TP 1.5%→0.8% |
| 2 | `7d94ff5` | 전략 | 킬 스위치 — 2연패 size 축소, 3연패 당일 BUY 중단 |
| 3 | `f38fd1b` | 전략 | 보유시간 차등화 — 공급계약 15분, 특허 30분, 주주환원 EOD |
| 4 | `aca12bb` | 전략 | 시간대별 전략 분리 — 비유동 시간대 spread 강화 + LLM 프롬프트 |
| 5 | `068ecc5` | 전략 | 체결/해지 구분 강화 — 복합 NEG 키워드 8개 + 문맥 분석 프롬프트 |
| 6 | `c64098b` | 전략 | SKIP 종목 후속 추적 — false negative 식별용 가격 스냅샷 |

### 변경 요약

**1. Trailing Stop + TP 조정**
- 고정 TP 1.5% → 0.8% (뉴스 반응 특성에 현실적)
- trailing stop activation 0.8% → 0.3% (더 빨리 수익 보호)
- 시간대별 trail 폭: 0~5분 0.3%, 5~30분 0.5%, 30분+ 0.7%
- `_get_trailing_stop_pct()` + `_entry_times` 추적

**2. 킬 스위치**
- 2연패 시 size_hint 한단계 다운 (L→M, M→S)
- 3연패 시 당일 BUY 완전 중단 (CONSECUTIVE_STOP_LOSS)
- `consecutive_loss_size_down`, `consecutive_loss_halt` config 추가
- `downgrade_size_hint()`, `get_kill_switch_size_hint()` 함수

**3. 보유시간 차등화**
- `hold_profile.py` 신규: 키워드→보유시간 매핑
- 공급계약/수주 15분, 특허/FDA 30분, 임상2상 20분, 자사주 소각 EOD
- pipeline에서 BUY 시 자동 적용

**4. 시간대별 전략 분리**
- 11:00~14:00 비유동 시간대: spread 한도 70% 강화 (MIDDAY_SPREAD_TOO_WIDE)
- decision_strategy.txt: 장전/개장/비유동/마감 시간대별 규칙 추가

**5. 체결/해지 구분 강화**
- buckets.json: 공급계약 해제/파기, 납품계약 해지, 수주계약 해지 등 8개 NEG 키워드
- decision_strategy.txt: 체결/해지/판매/구매 문맥 분석 규칙
- NEG_STRONG > POS_STRONG 우선순위로 "공급계약 해지" 확실히 NEG 처리

**6. SKIP 종목 후속 추적**
- SKIP된 POS_STRONG/POS_WEAK 종목 "skip_" 프리픽스로 가격 추적
- close 시점 수익률로 false negative 식별 가능

### 전체 누적 성과 (세션 1~6)

| 항목 | 시작 | 현재 |
|------|------|------|
| 테스트 | 427 (1 fail) | **490 passed, 0 failed** (+63) |
| 커밋 수 | 0 | **25 commits** |
| main.py LOC | 1,194 | **330** (72% 감소) |
| 전략 모듈 | 없음 | **hold_profile.py, 킬스위치, trailing stop** |
| 키워드 | 477개 | **485+개** (NEG 복합 키워드 추가) |

## 현재 상태
- **브랜치:** main
- **테스트:** 490 passed, 0 failed
- **마지막 커밋:** `c64098b` feat: SKIP 종목 후속 추적

## 잔여 기술 부채

### P1 — 전략 검증
1. **2주 룰 freeze + 데이터 수집** — 새 전략으로 100건+ 거래 필요
2. **replay 검증** — 기존 데이터로 trailing stop / hold profile 시뮬레이션

### P2 — 기능/전략
3. **Paper → 소액 Live 전환 준비** — KIS live API 키 설정, 주문 실행 모듈
4. **텔레그램 알림 품질 개선** — BUY 시그널에 "왜 BUY인지" 이유 추가
5. **소형주 집중** — ADV 500~2000억 구간 confidence 보너스 (brainstorm에서 P1)
6. **확률 기반 진입** — 뉴스 후 2~5분 관찰 후 진입 (구현 복잡)

### P3 — 제품 방향
7. **외부 사용자 확보** — 텔레그램 채널에 지인 1~3명 초대
8. **정보 서비스 pivot 검토** — AI 공시 분석 알림 서비스
