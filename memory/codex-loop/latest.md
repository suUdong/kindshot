Hypothesis: `scripts/strategy_comparison.py` is still making decisions from stale hardcoded exit rules. Reusing the same exit classifier and hold-profile-aware horizons as `strategy_observability` should keep future comparison reports aligned with the current live/reporting strategy surface.

Changed files:
- `docs/backtest-analysis.md`
- `scripts/strategy_comparison.py`
- `tests/test_strategy_comparison.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Targeted verification:
  - `source .venv/bin/activate && python -m pytest tests/test_daily_report.py tests/test_strategy_comparison.py -q` passed (`3 passed`)
- Full verification:
  - `source .venv/bin/activate && python -m pytest -q` passed (`572 passed, 1 warning`)
- Diagnostics:
  - affected files returned `0` LSP diagnostic errors

Risk and rollback note:
- This slice changes comparison/report output, not live execution behavior.
- Historical comparison numbers may shift because the stale hardcoded TP/SL/hold rules are removed.
- Roll back by restoring the old `compute_exit()` logic in `scripts/strategy_comparison.py` and removing `tests/test_strategy_comparison.py`.
