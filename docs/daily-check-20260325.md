# Daily Check — 2026-03-25

## Executive Summary

- `2026-03-25` 로컬 워크스페이스에는 운영 로그 `logs/kindshot_20260325.jsonl` 이 없다.
- 대신 오늘자 `data/runtime/context_cards/20260325.jsonl` 와 `data/runtime/price_snapshots/20260325.jsonl` 만 있는데, 둘 다 테스트 산출물이다.
  - `context_cards`: 65 rows, 전부 `run_id=test_run`, 단일 `event_id=4eb4648d2e75cbce`
  - `price_snapshots`: 115 rows, 전부 `run_id=run1`, 단일 `event_id=evt1`
- 따라서 `2026-03-25` 실제 운영 이벤트 흐름은 로컬만으로 재구성할 수 없다. 이 자체가 데이터/로그 수집 이슈다.
- 다만 최근 마지막 실제 운영 로그(`2026-03-19`)를 보면 `POS_STRONG` 이벤트는 66건 들어왔지만 40건이 `ADV_TOO_LOW` 로 잘려 `LLM`까지 5건만 도달했다.
- 추가로 로컬 `.env` 의 `ADV_THRESHOLD=50억` 이 코드 기본값 `5억`보다 10배 높아 최근 강한 촉매까지 과도하게 차단하고 있었다.
- 단일 가설로 `POS_STRONG` 에만 별도 ADV 하한 `20억` 을 적용하도록 수정했다. `POS_WEAK` 는 기존 `50억` 기준을 유지한다.

## 1. Today Log Analysis (`2026-03-25`)

### 1.1 Local artifact status

| Item | Status | Notes |
|------|--------|-------|
| `logs/kindshot_20260325.jsonl` | **missing** | 실제 운영 이벤트/결정 로그 없음 |
| `data/runtime/context_cards/20260325.jsonl` | present | 테스트 fixture 오염 |
| `data/runtime/price_snapshots/20260325.jsonl` | present | 테스트 fixture 오염 |

### 1.2 What that means

- 오늘 실제로 몇 건의 이벤트가 들어왔는지
- 어디서 필터되었는지
- LLM BUY/SKIP 비율이 어땠는지
- 킬스위치가 발동했는지

위 4가지는 **`2026-03-25` 로컬 운영 로그가 없어서 정확 집계 불가**다.

현재 로컬이 보여주는 오늘 데이터는 테스트 러닝이 runtime artifact 경로를 오염시킨 흔적뿐이다. 오늘 0 BUY를 진단하려면 서버/운영 환경의 `logs/kindshot_20260325.jsonl` 동기화 또는 해당 로그 경로 확인이 필요하다.

## 2. Latest Real Runtime Day (`2026-03-19`) Breakdown

로컬에 존재하는 마지막 실제 운영 로그는 `2026-03-19` 다.

### 2.1 Event intake and filter path

| Stage | Count |
|------|------:|
| total events | 232 |
| bucket-stage filtered | 137 |
| quant-stage filtered | 47 |
| guardrail-stage filtered | 27 |
| reached LLM / decision | 21 |
| BUY | 2 |
| SKIP | 19 |

### 2.2 Bucket distribution

| Bucket | Count |
|------|------:|
| POS_STRONG | 66 |
| POS_WEAK | 29 |
| NEG_STRONG | 6 |
| NEG_WEAK | 4 |
| IGNORE | 61 |
| UNKNOWN | 66 |

### 2.3 Where it filtered out

Top skip reasons on `2026-03-19`:

| Reason | Count |
|------|------:|
| IGNORE_BUCKET | 61 |
| UNKNOWN_BUCKET | 54 |
| ADV_TOO_LOW | 45 |
| MARKET_BREADTH_RISK_OFF | 14 |
| CORRECTION_EVENT | 12 |
| INTRADAY_VALUE_TOO_THIN | 8 |
| NEG_BUCKET | 6 |
| CHASE_BUY_BLOCKED | 5 |

### 2.4 POS_STRONG choke point

`POS_STRONG` 66건만 따로 보면:

| POS_STRONG stage | Count |
|------|------:|
| quant filtered | 41 |
| guardrail filtered | 20 |
| reached LLM | 5 |

Top `POS_STRONG` skip reasons:

| Reason | Count |
|------|------:|
| ADV_TOO_LOW | 40 |
| MARKET_BREADTH_RISK_OFF | 8 |
| INTRADAY_VALUE_TOO_THIN | 7 |
| CHASE_BUY_BLOCKED | 5 |

결론적으로 최근 실제 병목은 `POS_STRONG` 이 들어오지 않은 게 아니라, **들어온 뒤 ADV 문턱에서 과도하게 잘린 것**이다.

## 3. LLM Decision Stats

### 3.1 `2026-03-19`

| Metric | Value |
|------|------:|
| total decisions | 21 |
| BUY | 2 |
| SKIP | 19 |
| BUY ratio | 9.5% |
| SKIP ratio | 90.5% |

Bucket별 LLM 도달 건수:

| Bucket | Decisions | BUY |
|------|------:|------:|
| POS_STRONG | 5 | 2 |
| POS_WEAK | 16 | 0 |

### 3.2 Recent 7 logged days

로컬에 있는 최근 7개 실제 로그 파일(`20260311`, `20260312`, `20260313`, `20260316`, `20260317`, `20260318`, `20260319`) 기준:

| Date | BUY | SKIP | BUY% |
|------|----:|-----:|-----:|
| 2026-03-11 | 1 | 0 | 100.0% |
| 2026-03-12 | 5 | 1 | 83.3% |
| 2026-03-13 | 0 | 0 | 0.0% |
| 2026-03-16 | 5 | 0 | 100.0% |
| 2026-03-17 | 4 | 0 | 100.0% |
| 2026-03-18 | 6 | 7 | 46.2% |
| 2026-03-19 | 2 | 19 | 9.5% |

합계:

| Metric | Value |
|------|------:|
| total decisions | 50 |
| BUY | 23 |
| SKIP | 27 |

## 4. Kill Switch Check

- 로컬 실제 로그(`20260310`~`20260319`)에서 `CONSECUTIVE_STOP_LOSS` 는 **0건**이다.
- `guardrail_state.json` 도 로컬에 남아 있지 않다.
- 따라서 로컬 기준으로는 최근 0 BUY의 주원인을 킬스위치로 볼 근거가 없다.
- 오늘(`2026-03-25`) 킬스위치 상태는 운영 로그 부재 때문에 별도 확정 불가다.

## 5. Threshold Review and Change

### 5.1 Why adjustment was needed

- 현재 로컬 `.env` 는 `ADV_THRESHOLD=50억`.
- 이 값은 코드 기본값 `5억`보다 10배 높다.
- 최근 7 logged days 기준 `ADV_TOO_LOW` 는 240건 발생했다.
- 같은 기간 `POS_STRONG` 은 561건 있었는데, `ADV=20억` 기준이면 그중 **42건**이 기존 `ADV_TOO_LOW` 차단에서 벗어날 수 있다.
- `2026-03-19` 하루만 봐도 `POS_STRONG` 중 **9건**이 `20억` 기준에서는 추가 후보가 된다.

실제 예시:

- `290690` 소룩스 관련 인수/경영권 뉴스: ADV 약 24~25억
- `004690` 삼천리 자사주 소각 관련 뉴스: ADV 약 30억
- `011330` 유니켐 자사주 소각 관련 뉴스: ADV 약 43억

### 5.2 Applied change

단일 가설만 적용했다:

- `POS_STRONG` 에 한해서만 ADV 하한을 `20억` 으로 완화
- `POS_WEAK` 및 기타 경로는 기존 일반 ADV 하한 유지

구현 방식:

- `Config.pos_strong_adv_threshold` 추가
- `Config.adv_threshold_for_bucket()` 추가
- `quant_check()` 와 `check_guardrails()` 가 bucket별 ADV 하한을 받도록 확장
- pipeline 에서 `POS_STRONG` 만 override 전달

## 6. Validation

실행 결과:

- `source .venv/bin/activate && python -m pytest tests/test_strategy_observability.py tests/test_daily_report.py tests/test_hold_profile.py tests/test_config.py tests/test_quant.py tests/test_guardrails.py tests/test_pipeline.py tests/test_price.py -q`
  - `150 passed`
- `source .venv/bin/activate && python -m pytest -q`
  - `527 passed, 1 warning`

추가로 잠근 테스트:

- `POS_STRONG` 는 20억~50억 ADV 구간에서도 통과 가능
- `POS_WEAK` 는 같은 ADV에서도 여전히 `ADV_TOO_LOW`
- pipeline 이 `POS_STRONG` 에만 완화된 ADV 값을 guardrail까지 전달

## 7. Strategy Activity Status

전략 동작 현황은 이제 `deploy/daily_report.py` 와 텔레그램 요약에도 포함된다.
이 요약은 **현재 env가 아니라 report code에 고정된 strategy config**(`TP 0.8`, `SL -1.0`, trailing `0.3/0.5/0.7`, activation `0.3`, default hold `30m`)로 재구성하므로 과거 로그를 다시 읽어도 숫자가 흔들리지 않는다.

### 7.1 Latest real runtime day (`2026-03-19`)

| Strategy | Status |
|------|------|
| Trailing Stop | 0회 |
| Take Profit | 0회 |
| Stop Loss | 0회 |
| Max Hold | 1회 |
| 보유시간 차등 적용 | 1건 (`EOD:1`) |
| 킬스위치 halt | 0회 |
| 시간대별 guardrail | `midday_spread=0`, `market_close_cutoff=0` |
| 체결/해지 NEG 재분류 | 0건 |
| SKIP 추적 스케줄 | 0건 |

### 7.2 Recent 7 logged days aggregate

로컬에 남아 있는 최근 7개 실제 로그(`20260311`~`20260319`) 기준:

| Strategy | Count / Status |
|------|------|
| Trailing Stop | 2회 |
| Take Profit | 2회 |
| Stop Loss | 4회 |
| Max Hold | 4회 |
| 보유시간 차등 적용 | 18건 (`15m:13`, `30m:1`, `EOD:4`) |
| 킬스위치 halt | 0회 |
| 시간대별 guardrail | `midday_spread=0`, `market_close_cutoff=7` |
| 체결/해지 NEG 재분류 | 9건 |
| SKIP 추적 스케줄 | 0건 |

해석:

- `Trailing Stop`, `TP`, `Max Hold`, `보유시간 차등`, `시간대별 컷오프`, `체결/해지 NEG 재분류` 는 로컬 실제 로그 기준으로 동작 흔적이 확인된다.
- `킬스위치 halt` 는 최근 로그 기준 0건이다.
- `SKIP 추적` 이 0건인 것은 available log window가 해당 기능 추가(`2026-03-24`) 이전이기 때문이다. 기능 미동작의 증거로 해석하면 안 된다.

## 8. Changed Files

- `src/kindshot/config.py`
- `src/kindshot/quant.py`
- `src/kindshot/guardrails.py`
- `src/kindshot/pipeline.py`
- `src/kindshot/hold_profile.py`
- `src/kindshot/strategy_observability.py`
- `tests/test_config.py`
- `tests/test_quant.py`
- `tests/test_guardrails.py`
- `tests/test_pipeline.py`
- `tests/test_hold_profile.py`
- `tests/test_strategy_observability.py`
- `tests/test_daily_report.py`
- `tests/test_price.py`
- `.env.example`
- `deploy/daily_report.py`
- `docs/daily-check-20260325.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

## 9. Risks and Rollback

### Risks

- 오늘(`2026-03-25`) 실제 운영 로그가 로컬에 없어서, 이번 보고서는 최근 실제 운영일(`2026-03-19`)을 기준으로 병목을 추정했다.
- `POS_STRONG 20억` 완화는 signal flow 를 늘리지만, 중소형주 노이즈도 일부 유입시킬 수 있다.
- runtime artifact 경로가 테스트에 오염되는 문제는 이번 범위에서 수정하지 않았다.
- 전략 현황은 현재 일일 리포트에서 재구성되며, 과거 로그에는 일부 전략의 원시 runtime marker가 없어서 완전한 과거 복원은 제한된다.

### Rollback

- 이번 커밋을 revert 하면 된다.
- 논리적으로는 `pos_strong_adv_threshold` 관련 변경을 제거하고 uniform ADV 필터로 복귀하면 된다.
