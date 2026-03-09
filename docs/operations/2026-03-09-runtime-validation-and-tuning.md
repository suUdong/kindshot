# Kindshot Runtime Validation And Tuning

Date: 2026-03-09

## 1. Goal

This runbook covers:
- Reproducible validation commands.
- Runtime tuning for queue workers, LLM concurrency, and pykrx cache.
- Interpreting runtime counters emitted at shutdown.

## 2. Validation Flow

### 2.1 Quick command

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_checks.ps1
```

This runs:
1. Syntax compile check for `src` and `tests`.
2. `pytest -q` (if pytest is installed).

### 2.2 If tests cannot run

Common blockers in restricted environments:
- `pytest` not installed.
- Package installation blocked by network policy.

In that case:
1. Run compile-only check:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_checks.ps1 -SkipTests
```

2. Execute full tests in CI or in a network-enabled workstation.

## 3. Runtime Tuning Knobs

### 3.1 Queue and workers

Environment variables:
- `PIPELINE_WORKERS` (default: `4`)
- `PIPELINE_QUEUE_MAXSIZE` (default: `512`)

Guideline:
- Start `PIPELINE_WORKERS=4`.
- Increase gradually when `events_enqueued` rises much faster than `decisions_emitted`.
- Keep `PIPELINE_QUEUE_MAXSIZE` large enough to absorb short bursts, but not so large that backlog becomes invisible operationally.

### 3.2 LLM concurrency

Environment variable:
- `LLM_MAX_CONCURRENCY` (default: `2`)

Guideline:
- Keep `LLM_MAX_CONCURRENCY <= PIPELINE_WORKERS`.
- Raise only when upstream rate-limit and latency stay stable.
- If `llm_timeout` or `llm_call_error` increases after raising, roll back first.

### 3.3 pykrx cache

Environment variables:
- `PYKRX_CACHE_TTL_S` (default: `300`)
- `PYKRX_CACHE_MAX_SIZE` (default: `512`)

Behavior:
- TTL expiration + LRU eviction + periodic prune.

Guideline:
- Increase TTL to reduce pykrx calls when tickers repeat frequently.
- Increase max size only if unique ticker volume regularly exceeds current bound.

## 4. Runtime Counter Interpretation

`main.py` emits a `Runtime counters` log object on shutdown.

Key fields:
- `totals.events_seen`: disclosures observed from feed.
- `totals.events_enqueued`: events passed to queue workers.
- `totals.events_skipped`: skipped events total.
- `totals.decisions_emitted`: decision records written.
- `errors.llm_timeout`, `errors.llm_call_error`, `errors.llm_parse_error`, `errors.worker_exception`.
- `skip_stage.*`, `skip_reason.*` for skip distribution.

Baseline sanity checks:
1. `events_enqueued <= events_seen`
2. `decisions_emitted <= events_enqueued`
3. `worker_exception == 0` in steady-state runs
4. No sudden spikes in `LLM_*` skip stages

## 5. Recommended Rollout

1. Keep defaults first:
   - `PIPELINE_WORKERS=4`
   - `PIPELINE_QUEUE_MAXSIZE=512`
   - `LLM_MAX_CONCURRENCY=2`
2. Run at least one session and capture counters.
3. Tune one variable at a time.
4. Compare before/after using:
   - decision latency p95
   - LLM error rates
   - queue backlog indicators (`events_seen` vs `events_enqueued` vs `decisions_emitted`)
