Hypothesis: If the exit-parameter optimizer scores every candidate relative to the active baseline instead of mixing relative and absolute penalties, then Kindshot backtest analysis will produce a trustworthy recommendation even on all-negative windows; on the current local evidence, that trustworthy recommendation is to keep the active env-backed parameter block unchanged.

Changed files:
- `scripts/backtest_analysis.py`
- `tests/test_backtest_analysis.py`
- `docs/plans/2026-03-29-backtest-optimization-relative-scoring.md`
- `logs/daily_analysis/backtest_analysis_20260329`
- `logs/daily_analysis/backtest_analysis_20260329.json`
- `logs/daily_analysis/auto_tune_20260329.json`
- `logs/daily_analysis/monthly_full_strategy_backtest_20260329.txt`
- `logs/daily_analysis/monthly_full_strategy_backtest_20260329.json`
- `memory/codex-loop/backtest-optimization-20260329.md`
- `.omx/context/kindshot-backtest-analysis-parameter-optimization-20260329T002216Z.md`

Implementation summary:
- Fixed `scripts/backtest_analysis.py` so non-baseline exit candidates are scored on fully relative deltas versus the active baseline for total PnL, average PnL, profit factor, win rate, and drawdown magnitude.
- Added a regression in `tests/test_backtest_analysis.py` that reproduces the failure mode where a less-bad all-negative candidate must outrank the baseline.
- Re-ran the local analysis surfaces:
  - `backtest_analysis_20260329`
  - `auto_tune_20260329.json`
  - `monthly_full_strategy_backtest_20260329.{txt,json}`
- Refreshed evidence shows the current env-backed recommendation remains:
  - `MIN_BUY_CONFIDENCE=78`
  - `OPENING_MIN_CONFIDENCE=88`
  - `AFTERNOON_MIN_CONFIDENCE=80`
  - `CLOSING_MIN_CONFIDENCE=85`
  - `PAPER_TAKE_PROFIT_PCT=2.0`
  - `PAPER_STOP_LOSS_PCT=-1.5`
  - `TRAILING_STOP_ACTIVATION_PCT=0.5`
  - `TRAILING_STOP_EARLY_PCT=0.5`
  - `TRAILING_STOP_MID_PCT=0.8`
  - `TRAILING_STOP_LATE_PCT=1.0`
  - `MAX_HOLD_MINUTES=30`
  - `T5M_LOSS_EXIT_ENABLED=True`
  - `FAST_PROFILE_NO_BUY_AFTER_KST_HOUR=14`

Validation:
- `pytest -q tests/test_backtest_analysis.py tests/test_auto_tune_strategy.py tests/test_monthly_full_strategy_backtest.py` -> `12 passed`
- `python3 -m compileall src scripts tests` -> success
- changed-file diagnostics:
  - `scripts/backtest_analysis.py` -> 0 errors
  - `tests/test_backtest_analysis.py` -> 0 errors
- analysis reruns:
  - `python scripts/backtest_analysis.py --format both --output logs/daily_analysis/backtest_analysis_20260329` -> success
  - `python scripts/auto_tune_strategy.py --analysis logs/daily_analysis/backtest_analysis_20260329.json --format json --output logs/daily_analysis/auto_tune_20260329.json` -> success
  - `python scripts/monthly_full_strategy_backtest.py --lookback-months 3` -> success
- full suite note:
  - `pytest -q` hit unrelated existing failures in `tests/test_replay.py`, `tests/test_strategy_comparison.py`, and `tests/test_strategy_observability.py` because other dirty-worktree files outside this slice currently disagree on hold-profile / strategy-observability defaults

Simplifications made:
- Fixed the optimizer by correcting the scoring model instead of adding special-case tie breakers or post-hoc candidate overrides.
- Left runtime config, `.env`, deploy surfaces, and paper/live execution behavior untouched; this slice improves analysis correctness and evidence quality only.

Remaining risks:
- The evidence window is still only `14` local log days out of the requested `3` calendar months, so optimization confidence remains limited by sample coverage.
- The opaque LLM replay path remains blocked locally by Anthropic credit failure, so the monthly report still relies on logged BUY decisions plus deterministic current logic for approximation.
- Full-suite failures exist outside this slice in already-modified files; this run does not resolve them because they are unrelated to the backtest-analysis scoring change.

Rollback note:
- Revert the scoring/test/doc changes and discard the refreshed local analysis artifacts. No runtime or deploy rollback is required because this slice does not change live behavior.
