# 2026-03-27 Guardrail Recalibration

## Intent

이번 slice는 "무조건 BUY 늘리기"가 아니라, 가드레일이 막아야 할 것과 풀어도 되는 것을 데이터로 다시 나누는 작업이다. 핵심은 `LOW_CONFIDENCE` 와 `FAST_PROFILE_LATE_ENTRY` 의 과차단을 줄이되, 추격매수/유동성/장마감 하드스톱은 그대로 유지하는 것이다.

## Current Evidence

- `2026-03-26` server closeout:
  - inline BUY `15`
  - passed `0`
  - blocked `15`
  - blocker mix: `LOW_CONFIDENCE 13`, `MARKET_CLOSE_CUTOFF 2`
- `2026-03-27` current server log:
  - inline BUY `26`
  - passed `5`
  - blocked `21`
  - blocker mix: `LOW_CONFIDENCE 10`, `FAST_PROFILE_LATE_ENTRY 5`, `OPENING_LOW_CONFIDENCE 2`, `CHASE_BUY_BLOCKED 2`, `MARKET_CLOSE_CUTOFF 1`, `ORDERBOOK_TOP_LEVEL_LIQUIDITY 1`
- local reconstructed executed BUY history:
  - `14` trades
  - win rate `28.6%`
  - avg pnl `-0.149%`
- available shadow outcome coverage is still sparse:
  - `2026-03-27` server `shadow_analysis.py` currently reconstructs only `2` blocked BUYs
  - both are flat `0.00%`, so opportunity-cost evidence is still partial

## Decision

이번 run의 bounded hypothesis:

`supportive market에서 confidence 계열 문턱을 소폭 완화하고 fast-profile late-entry cutoff를 장중 범위에서만 늘리면, 하드 리스크 규칙은 유지한 채 과도한 차단을 줄일 수 있다.`

## What Changes

### 1. Analysis surface

기존 `scripts/backtest_analysis.py` 에 guardrail review 섹션을 추가한다.

필수 출력:
- passed BUY vs blocked BUY count
- blocker counts by reason
- blocker counts by confidence band
- blocker counts by hour/hour bucket
- passed BUY summary
- shadow-backed blocked BUY summary
- shadow coverage note

### 2. Runtime guardrail dynamics

가드레일은 기존 정적 config를 base로 쓰되, market snapshot이 supportive일 때만 동적으로 완화한다.

완화 대상:
- base `min_buy_confidence`
- `afternoon_min_confidence`
- `fast_profile_no_buy_after`

유지 대상:
- `CHASE_BUY_BLOCKED`
- `ORDERBOOK_TOP_LEVEL_LIQUIDITY`
- `MARKET_CLOSE_CUTOFF`
- pipeline의 `MARKET_BREADTH_RISK_OFF`

### 3. Logging / observability

dynamic relaxation이 켜졌을 때 effective threshold를 로그로 남긴다. 운영자는 서버 로그만 보고도 "오늘 왜 통과했는지"를 추적할 수 있어야 한다.

## Rollout

1. extend analysis/reporting
2. implement dynamic guardrail profile
3. add/update tests
4. compile + targeted tests + full pytest
5. commit + push
6. rsync deployment + service restart + server smoke checks

## Rollback

- revert this slice's runtime and analysis changes
- redeploy previous revision
- `.env` values and `deploy/` paths are untouched in this slice
