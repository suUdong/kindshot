# Kindshot Codex Session State

## Current Session

- Branch: `main`
- Phase: `Backtest Analysis`
- Focus: use the latest 7 logged trading days to isolate one underperforming strategy slice and land a reversible guardrail backed by tests.
- Active hypothesis: fast-decay `15m` hold profiles should not open new BUYs after `14:00` KST, and time-based guardrails should evaluate against the event time instead of wall-clock `now()`.

## Environment

- Host: local workspace
- Runtime target: Python `3.11+`
- Current local venv: `.venv` uses Python `3.12.3`
- Validation status:
  - `source .venv/bin/activate && python -m pytest tests/test_config.py tests/test_guardrails.py tests/test_pipeline.py -q` passed (`110 passed`)
  - `source .venv/bin/activate && python -m pytest -q` passed (`551 passed, 1 warning`)
- Tooling note: local `.venv` remains the default runner for follow-up verification.

## Last Completed Step

- Read `SESSION_HANDOFF.md` and reconstructed the most recent locally available 7-log window: `2026-03-11`, `2026-03-12`, `2026-03-13`, `2026-03-16`, `2026-03-17`, `2026-03-18`, `2026-03-19`.
- Wrote `docs/backtest-analysis.md` with per-strategy counts, win rates, and approximate PnL.
- Identified the worst repeatable cohort:
  - `15m` hold profile after `14:00` KST
  - `5` trades, `0` wins, avg `-0.796%`, sum `-3.979%`
- Implemented one bounded change:
  - added config-backed `FAST_PROFILE_*` cutoff defaults
  - added `FAST_PROFILE_LATE_ENTRY` guardrail
  - made time-based guardrails consume injected event time in both runtime pipeline and replay
- Added regression coverage for fast-profile cutoff and guardrail argument propagation.

## Next Intended Step

- Observe the next real runtime logs to confirm whether `FAST_PROFILE_LATE_ENTRY` appears on late-session contract/suju headlines and whether the blocked cohort stays net-negative.
- If sufficient new runtime logs accumulate, re-run the same 7-day analysis on post-change data before considering any further strategy tuning.

## Notes

- Local workspace still lacks `logs/kindshot_*.jsonl` after `2026-03-19`; do not describe later dates as verified runtime evidence from this machine.
- Untracked workspace items under `.omc/`, `.omx/`, `data/`, `docs/superpowers/plans/`, `IMPROVEMENT_ANALYSIS.md`, and `scripts/auto-improve.sh` were left untouched.
