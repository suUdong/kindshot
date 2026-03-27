# 2026-03-28 Monthly Full-Strategy Backtest

## Goal

Produce one reproducible local report path that answers four operator questions in one run:

1. what the currently shipped strategy would have earned over the recent one-month local evidence window
2. how `v64` through `v70` compare on the same trade set
3. which parameter set is currently best-supported by the local evidence
4. whether the repository is in a tested, committable, push-ready state after generating the artifacts

## Why This Slice

- The user explicitly requested a full-strategy backtest, version comparison, parameter confirmation, and `commit+push`.
- Existing local tooling is fragmented:
  - `trade_db.py` + `version_report.py` cover versioned exit simulation
  - `backtest_analysis.py` covers trade reconstruction and parameter ranking
  - `entry_filter_analysis.py` covers stale-entry / liquidity tuning
  - `llm_prompt_eval.py` covers prompt-path evaluation
- There is no single command that combines these into one operator-facing artifact.

## Current State

- Local logs cover `2026-03-10` through `2026-03-27`.
- Reconstructable executed BUY trades with usable forward price data currently exist only on a smaller subset of dates.
- Current live replay of the opaque LLM path is blocked in this environment:
  - NVIDIA path is unconfigured locally
  - Anthropic path returns a `400 invalid_request_error` due to insufficient credits
- `trade_db.backfill_from_logs()` only reads external runtime snapshot files and ignores embedded `price_snapshot` rows already present in `logs/kindshot_*.jsonl`.
  - Result: local version comparison currently backfills rows but leaves `exit_ret_pct` null for most historical trades.

## Constraints

- Paper-only. No live-order execution.
- No `deploy/`, secrets, or credential handling changes.
- Keep the diff narrow and reversible.
- Use only local evidence for the final report; do not pretend the opaque LLM path was replayed when it was not.

## Design

### 1. Fix trade backfill fidelity

- Update `backfill_from_logs()` so it first loads embedded `price_snapshot` rows from each log file, then overlays external runtime snapshot files when they exist.
- This keeps version-comparison logic aligned with other analysis scripts that already consume both sources.

### 2. Add one unified monthly backtest script

- Add a new local script that:
  - selects the latest available local 30-day-ish window from `logs/kindshot_*.jsonl`
  - reconstructs executed BUY trades using `backtest_analysis.py`
  - generates `v64`~`v70` comparison metrics on the same trade set
  - estimates current-strategy performance using:
    - current exit logic
    - current entry guardrails
    - current risk-v2 portfolio state progression
    - current deterministic pre-LLM skip logic
    - historical logged BUY decisions as the proxy for the opaque model action where fresh LLM replay is unavailable
- The report must clearly separate:
  - directly replayed / reconstructed logic
  - approximated logic caused by the LLM-credit blocker

### 3. Current-strategy offline approximation

- For each reconstructable historical BUY candidate:
  - reuse the logged BUY decision confidence / size as the opaque-model proxy
  - re-apply current deterministic preflight skip logic
  - re-apply current `check_guardrails()` with current config
  - simulate current exit behavior
  - simulate portfolio guardrail state over time:
    - max positions
    - same-stock rebuy
    - daily loss floor
    - consecutive-loss halt
    - recent-win-rate tightening
- Use sector concentration only when sector metadata exists in local runtime context; otherwise treat it as unavailable and report that explicitly.

### 4. Optimal parameter summary

- Report the locally best-supported exit candidate from `backtest_analysis.py`.
- Include the currently configured entry/risk thresholds from `Config`.
- Include the latest saved LLM prompt-eval artifact summary and blocker state.

## Logging / Artifacts

- Write JSON and text outputs under `logs/daily_analysis/`.
- Include:
  - analysis window
  - replay/reconstruction limitations
  - current-strategy estimate
  - `v64`~`v70` comparison table
  - optimal parameter summary

## Validation

1. targeted pytest for `trade_db` and the new monthly report script
2. `python3 -m compileall src scripts tests`
3. local execution of the new monthly report script
4. full `pytest -q`
5. diagnostics on changed Python files
6. update `memory/codex-loop/latest.md`
7. commit with Lore trailers
8. push `main`

## Rollback

- Revert the backfill/reporting commit.
- Delete the generated analysis artifacts if desired.
- No runtime deployment rollback is required because this slice changes only local analysis/reporting surfaces.
