Hypothesis: after blocking late `15m` fast-decay entries, the weakest remaining keyword-specific cohort is M&A. `인수` / `합병` should not keep the shareholder-return `EOD` hold profile; shortening them to `30m` should keep the initial reaction while reducing close-time giveback.

Changed files:
- `docs/backtest-analysis.md`
- `src/kindshot/hold_profile.py`
- `tests/test_hold_profile.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Follow-up analysis after the late `15m` cutoff:
  - remaining trades: `18`
  - remaining result: `11` wins / `7` losses, sum return `+4.128%`
  - M&A cohort (`인수` / `합병`): `2` trades, `50.0%` win rate, avg `-1.068%`, sum `-2.136%`
  - what-if with `30m` M&A hold: sum return `+4.128% -> +5.756%`
- Test commands:
  - `source .venv/bin/activate && python -m pytest tests/test_hold_profile.py tests/test_strategy_observability.py tests/test_daily_report.py -q` passed (`16 passed`)
  - `source .venv/bin/activate && python -m pytest -q` passed (`569 passed, 1 warning`)
- Diagnostics:
  - affected files returned `0` LSP diagnostic errors

Risk and rollback note:
- The M&A sample is small (`2` trades), so this is a narrow evidence-backed tweak, not a broad behavioral rewrite.
- Roll back by restoring `인수` / `합병` to `0` in `hold_profile.py` and reverting the accompanying doc/test updates.
