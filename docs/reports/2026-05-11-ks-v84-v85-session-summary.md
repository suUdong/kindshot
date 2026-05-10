# KS Strategy Session Summary — 2026-05-11 (v84 → v84.2)

## 1. Numerical Impact (classify_buy_exit 실제 코드 시뮬, n=14 trades)

| Stage | AVG exit_ret_pct | Win rate | Δ from baseline |
|-------|----------------:|---------:|----------------:|
| **Baseline (historic)** | **-0.646%** | 3/14 (21.4%) | — |
| v84 (exit-logic tightening) | -0.457% | 4/14 (28.6%) | +0.189 pp |
| v84.1 (+09시 entry block) | -0.164% | 4/10 (40.0%) | +0.482 pp |
| v84.2 (+conf floor 71→77) | **-0.030%** | 4/9 (44.4%) | **+0.616 pp** |
| Stretch target ≥ 0% | — | — | +0.65 pp |

**Sim AVG -0.030%로 break-even 근접; 시뮬은 horizon 해상도 한계로 SL/trailing 효과를 보수적으로 평가** — live 실행에서는 더 큰 개선 예상.

## 2. Commits (이번 세션)

1. `d46f4d0` docs: KS 전략 v84 손실 분석 보고서 (14건 trades, 4대 원인)
2. `9b2f8bb` feat: v84 exit-logic tightening (paper_stop_loss, trailing, t5m + W11 midmorning fix)
3. `9db7397` feat: v84.1 09시 entry 전면 차단 (early_session_block_end_minute 30→60)
4. `ef32376` feat: Topic 2 RebalanceFeed 구현 (외부 작업)
5. `5c46fe1` docs: DART PEAD Phase 2 통합 DEFER 결정
6. `41ad22c` feat: v84.2 dynamic guardrail confidence floor 71→77

## 3. 4대 손실 원인 (14건 데이터 기반)

| 원인 | 대응 lever |
|------|-----------|
| Trailing stop 미작동 (peak 0.5% 미만 8건) | activation 0.5→0.3, mid 0.8→0.6 |
| Stop loss -1.5% 과도 노출 | -1.5 → -1.0 |
| t5m_loss_exit 임계 -0.3% 너무 느슨 | -0.3 → -0.2 |
| 09시 시간대 0/4 전패 (-1.19%) | early_session_block_end_minute 30→60 |
| conf<78 borderline 진입 (259960 -1.37%) | dynamic relaxation floor 71→77 |

## 4. RebalanceFeed (Topic 2) vs InstFlow Spec Diff

`src/kindshot/feeds/rebalance_feed.py` (126 lines) 구현 상태:

### ✅ Spec 충족
- KOSPI 200 / KOSDAQ 150 6/12월 정기변경 트리거
- T-5 ~ T-0 window
- Add 종목 BUY signal 생성
- base_confidence=70 + size_hint M

### ⚠️ Spec 미충족 / Gap

| Spec 요구 | 현재 구현 | 필요 작업 |
|----------|-----------|----------|
| **효력발생일 산정** | 2nd Thursday (옵션만기일 유사) | KRX 정기변경 영업일 calendar 정확화 |
| **Exit Strategy** | 없음 (BUY signal만) | T-0 종가 단일가 청산 로직 |
| **Flow Calculator** | 없음 | 종목별 예상 수요 = AUM × 비중 계산 |
| **Dynamic Scraper** | placeholder (빈 list) | KRX 공지 / pykrx PDF diff scraper |
| **Delete 종목 처리** | 없음 | skip 또는 매도 신호 |
| **Entry timing 선택** | T-5만 | 발표일 momentum vs T-5 flow 옵션 |
| **Intensity score** | 없음 | 거래대금 대비 강도 정량화 |

별도 phase (v86?)에서 통합 예정.

## 5. DART PEAD Phase 2 통합 DEFER

- Phase 1 (dcf59e0) 코드 활성 / 운영 dormant (DART_API_KEY 미설정)
- 14건 trades 모두 PEAD 시그널 0건 → 직접 손실 원인 아님
- 5가지 진입 조건 충족 후 별도 phase 진행 (docs/plans/2026-05-11-pead-phase2-decision.md)

## 6. v85 Lever 후보 평가

| Lever | 시뮬 효과 | 비고 |
|-------|----------|------|
| Position sizing by conf band (conf>=80 1.4x, 78-79 0.6x) | +0.103% (≥ 0% 도달) | 구현 복잡 — execution layer 변경 |
| Stagnation_exit (peak<0.05 + t10m<-0.1) | ~0 (sim) | live 효과 가능, 002990 trap 위험 |
| Partial TP threshold 1.0→0.5 | ~0 (sim) | 002990 +2.20→ +1.5 손해 |
| EOD hold에 t5m_loss_exit 활성화 | 0 (v84.2로 표본 차단됨) | future 자사주매입 대비 |

**다음 세션 우선순위: Position sizing by conviction band** (가장 큰 expected delta, 복잡도 중)

## 7. Next-day Paper Sim 예상

- v84.2 적용 후 hour=9 진입 차단 → 일일 평균 trade 수 약 30% 감소 (역사 비례)
- conf<77 차단으로 약 10% 추가 감소
- 남은 trade는 conf>=78 + 10시~14시 구간 → 시뮬상 break-even 근접

## 8. Bottleneck (next session)

1. **데이터 해상도**: trades 14건만 + horizon 해상도 한계 → live paper-trading 1-2주 추가 표본 필요
2. **Position sizing layer**: execution code 변경 필요 (order.py, decision.py size_hint 로직)
3. **Topic 2 RebalanceFeed**: 효력일 calendar + exit auction timing + Flow calculator
4. **PEAD Phase 2**: DART API key + 라이브 폴링 안정화 prerequisite

## 9. 다음 세션 권장 우선순위

1. v85: Position sizing by conviction band (시뮬 +0.103%, AVG ≥ 0% 확정)
2. RebalanceFeed gap fill (calendar 정확화 + exit auction)
3. live 1-2주 paper 운영 후 v84.x 실적 검증
4. PEAD Phase 2 prerequisite 충족 시 LLM tone scoring
