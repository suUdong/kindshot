# Kindshot 종합 리포트 (2026-03-29)

## 1. Executive Summary

- 현재 로컬 근거 기준으로 Kindshot의 최근 성과는 아직 음수 구간이다. 최근 `14`일 집계는 `27`건 BUY, 승률 `30%`, 합계 `-8.62%`, PF `0.44`였다.
- 더 깊은 시뮬레이션 기준으로도 추세는 같다. `3`개월 요청 대비 실제 커버리지는 `2026-03-10`~`2026-03-27`의 `14/91`일(`15.38%`)뿐이지만, 현재 워크트리 기준 in-memory 재계산에서는 accepted `4`, blocked `10`, 비용 반영 후 순수익률 `-0.827%`, 순승률 `0%`였다.
- 운영 측면에서는 월요일 `2026-03-30` 오픈 전 read-only readiness 경로는 정리되었지만, 실제 라이브 세션에서 `news_signal`, 섹터 모멘텀, 거래량 필드가 채워진 현재-day 구조화 로그는 아직 확인되지 않았다.
- 따라서 지금 시점의 결론은 "전략 파라미터를 더 바꾸기보다, 월요일 첫 신선한 live/paper 이벤트를 잡아 semantic-enrichment가 실제 런타임에 반영되는지 먼저 확인해야 한다"이다.

## 2. 근거 범위

이 리포트는 아래 근거를 종합했다.

- `memory/codex-loop/roadmap.md`
- `memory/codex-loop/session.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/ops-backlog.md`
- `memory/codex-loop/backtest-optimization-20260329.md`
- `docs/weekly-performance.md`
- `docs/plans/2026-03-29-monday-server-readiness-summary.md`
- `logs/daily_analysis/monthly_full_strategy_backtest_20260329.txt`
- `logs/daily_analysis/monthly_full_strategy_backtest_20260329.json`
- `logs/daily_analysis/backtest_analysis_20260329.json`
- `logs/daily_analysis/auto_tune_20260329.json`

## 3. 성과 스냅샷

### 3.1 최근 14일 운영 로그 집계

`python3 scripts/weekly_report.py --days 14` 기준:

| 지표 | 값 |
|---|---:|
| 활성일 | `10`일 |
| BUY | `27`건 |
| 승률 | `8/27 (30%)` |
| 평균 수익률 | `-0.32%` |
| 누적 수익률 | `-8.62%` |
| Profit Factor | `0.44` |
| 최고 거래 | `+2.70%` |
| 최저 거래 | `-3.05%` |

일별로는 `2026-03-20`이 `-6.13%`로 가장 나빴고, `2026-03-16`이 `+1.17%`로 가장 좋았다. 시간대별로는 `11시`대만 평균 `+0.56%`였고, `09시`대는 평균 `-0.75%`, `14시`대는 평균 `-1.81%`였다.

### 3.2 3개월 요청 기준 통합 백테스트 리포트

`python3 - <<'PY' ... build_report(PROJECT_ROOT, lookback_months=3) ... PY` in-memory 재계산 기준:

| 지표 | 값 |
|---|---:|
| 요청 윈도우 | `2025-12-27`~`2026-03-27` |
| 실제 커버 윈도우 | `2026-03-10`~`2026-03-27` |
| 커버 로그 일수 | `14 / 91` |
| candidate / accepted / blocked | `14 / 4 / 10` |
| gross 승률 | `50.0%` |
| gross 합계 | `-0.0447%` |
| net 승률 | `0.0%` |
| net 합계 | `-0.8270%` |
| net 총손익 | `-47,930 KRW` |

차단 사유는 `ADV_TOO_LOW=3`, `EARLY_SESSION_BLOCKED=3`, `LOW_CONFIDENCE=2`, `OPENING_LOW_CONFIDENCE=1`, `PRIOR_VOLUME_TOO_THIN=1`였다. 즉, 현재 워크트리는 저장된 `20260329` 아티팩트보다 장초반 진입을 더 보수적으로 막고 있고, 그 결과 거래 수는 줄었지만 비용 반영 후 순이익은 여전히 음수다.

참고로 저장된 `logs/daily_analysis/monthly_full_strategy_backtest_20260329.*` 아티팩트는 earlier state를 반영해 `accepted=6`, `blocked=8`로 남아 있다. 현재 미커밋 워크트리에는 `EARLY_SESSION_BLOCKED` 강화가 포함돼 있으므로, 본 리포트는 저장본보다 fresh in-memory 재계산값을 우선한다.

### 3.3 Exit / Bucket 관찰

`logs/daily_analysis/backtest_analysis_20260329.json` 기준:

- 전체 `14`건의 reconstructed BUY에서 승률은 `28.6%`, 총합은 `-2.086%`, PF는 `0.21`, MDD는 `-2.421%`였다.
- `STALE` 종료는 `4건` 모두 플러스였고 합계 `+0.57%`였다.
- 반대로 `T5M_LOSS_EXIT`는 `8건` 전부 마이너스였고 합계 `-1.023%`, `TRAILING`은 `1건 -1.105%`였다.
- BUY 버킷은 사실상 `POS_STRONG`에 집중되어 있고(`13/14`건), 이 버킷의 합계도 `-2.254%`로 아직 음수다.

핵심 해석은 단순하다. 초기 미세 모멘텀을 짧게 회수하는 경우만 일부 살아 있고, 조금만 더 보유하면 손익이 다시 눌리는 패턴이 이어지고 있다.

### 3.4 현재 권장 파라미터

`logs/daily_analysis/auto_tune_20260329.json` 기준 추천은 "기존 env 블록 유지"다.

| 항목 | 값 |
|---|---:|
| `MIN_BUY_CONFIDENCE` | `78` |
| `OPENING_MIN_CONFIDENCE` | `88` |
| `AFTERNOON_MIN_CONFIDENCE` | `80` |
| `CLOSING_MIN_CONFIDENCE` | `85` |
| `PAPER_TAKE_PROFIT_PCT` | `2.0` |
| `PAPER_STOP_LOSS_PCT` | `-1.5` |
| `TRAILING_STOP_ACTIVATION_PCT` | `0.5` |
| `TRAILING_STOP_EARLY_PCT` | `0.5` |
| `TRAILING_STOP_MID_PCT` | `0.8` |
| `TRAILING_STOP_LATE_PCT` | `1.0` |
| `MAX_HOLD_MINUTES` | `30` |
| `T5M_LOSS_EXIT_ENABLED` | `True` |
| `FAST_PROFILE_NO_BUY_AFTER_KST_HOUR` | `14` |

이 추천은 "좋아서 유지"가 아니라, 현재 근거 창에서 다른 후보가 더 낫다고 증명되지 않았기 때문에 baseline을 유지하는 보수적 결론이다.

## 4. 운영 상태

### 4.1 Monday 2026-03-30 readiness

`memory/codex-loop/latest.md`와 `docs/plans/2026-03-29-monday-server-readiness-summary.md` 기준:

- `scripts/server_monitor.py`는 이제 `kindshot` 서비스 상태, 대시보드 상태, inferred mode, `/health` 요약, runtime/poll/journal 정보를 read-only로 묶어 보여준다.
- 최근 검증 시 원격 서버는 `paper` 모드에서 healthy였고 polling heartbeat도 살아 있었다.
- 월요일 체크리스트의 절대 날짜는 `2026-03-30 (월)`로 정정되었다.

### 4.2 아직 닫히지 않은 운영 갭

- Sunday 기준 증거는 여전히 `events_seen=0`과 "structured runtime log 없음"이었다. 시장이 닫혀 있었으므로 설명은 가능하지만, semantic-enrichment가 실제 current-day 런타임 로그에 반영되는지 증명되지는 않았다.
- `memory/codex-loop/ops-backlog.md`의 유일한 `P0` active 성격 항목도 결국 "다음 live/paper 세션에서 decision records가 다시 찍히는지 확인"으로 귀결되며, 현재는 `BLOCKED`다.
- `deploy/verify-live.sh`는 older health payload shape를 일부 전제로 해서 rich readiness detail은 여전히 `scripts/server_monitor.py` 쪽이 더 정확하다.

## 5. 로드맵 상태

`memory/codex-loop/roadmap.md` 기준 현재 상태는 다음과 같다.

- Track: `NLP Signal Enrichment`
- Phase: `Post-Deployment Observation`
- Status: `In Progress (user override)`
- 핵심 목표: 배포된 NLP/sector/volume 경로가 다음 신선한 live item에서 실제로 채워지는지 확인

현재 우선순위는 전략 튜닝이 아니라 관측이다.

1. 월요일 `2026-03-30` 장중 첫 fresh paper item을 포착한다.
2. 해당 current-day runtime log row에 `news_signal`, sector momentum, volume 필드가 plausibly 채워지는지 본다.
3. 또다시 duplicate polling만 보이고 구조화 로그가 없으면 upstream feed freshness를 먼저 점검한다.

## 6. 현재 리스크

### 6.1 성과 리스크

- 최근 14일 운영 집계와 3개월 요청 백테스트 추정이 모두 음수다.
- `POS_STRONG` surface가 거래 대부분을 차지하지만 아직 누적 양수 전환에 실패했다.
- 비용 반영 후 순승률이 `0%`인 점은 짧은 gross edge가 실제 실행 비용을 못 이기고 있다는 뜻이다.
- 장초반 차단 강화로 거래 수를 줄인 현재 워크트리에서도 순손익은 아직 음수다.

### 6.2 근거 리스크

- 3개월 요청 대비 실제 로컬 커버리지가 `15.38%`뿐이라 일반화 신뢰도가 낮다.
- opaque LLM replay는 Anthropic credit 부족으로 막혀 있어, 현재 리포트는 logged BUY + deterministic guard/exit 재적용 중심이다.

### 6.3 운영 리스크

- Monday open 전까지는 live semantic-enrichment coverage가 아직 "추정" 단계다.
- `scripts/`를 원격에 영구 반영하려면 별도 sync가 필요하고, 그렇지 않으면 stdin 기반 one-liner 경로를 계속 써야 한다.

### 6.4 작업 트리 리스크

현재 워크트리는 이번 종합 리포트와 별개인 미커밋 변경을 포함한다.

- modified: `scripts/strategy_performance.py`
- modified: `src/kindshot/config.py`
- modified: `src/kindshot/pipeline.py`
- modified: `src/kindshot/unknown_review.py`
- modified: `tests/test_pipeline.py`
- modified: `tests/test_unknown_review.py`
- untracked: `docs/plans/2026-03-29-disable-news-weak.md`
- untracked: `memory/codex-loop/backtest-optimization-20260329.md`
- untracked: `ralph-loop.state.json`
- untracked: `tests/test_strategy_performance.py`

따라서 이번 리포트의 검증은 종합 리포트와 직접 연관된 스크립트/테스트에 한정해 읽는 것이 안전하다.

## 7. 결론과 다음 실행

현재 Kindshot의 최선의 다음 행동은 추가 전략 수정이 아니다. 이미 최신 분석은 baseline 유지 결론을 냈고, 남은 가장 큰 불확실성은 "배포된 signal enrichment가 live runtime에서 실제로 관측되는가"이다.

다음 실행 순서는 아래가 맞다.

1. 월요일 `2026-03-30` 장중 첫 fresh item을 `scripts/server_monitor.py`와 current-day runtime log로 확인한다.
2. decision/event row에 semantic-enrichment 필드가 채워졌다면 그 근거를 바탕으로 다음 전략 가설을 고른다.
3. 채워지지 않았다면 전략 튜닝 대신 feed freshness 또는 runtime wiring 관찰부터 다시 한다.

현 시점 평가는 다음 한 줄로 요약된다.

> Kindshot은 현재 "운영 관측은 거의 준비됐지만, 성과 edge와 live signal coverage는 아직 증명되지 않은 상태"다.
