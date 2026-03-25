Hypothesis: fast-decay `15m` hold-profile headlines (`공급계약`, `수주`, `납품계약`) lose edge after `14:00` KST. Blocking late-session BUYs for that profile should improve risk-adjusted returns, and the rule must use injected event time so runtime and replay evaluate the same window.

Changed files:
- `docs/backtest-analysis.md`
- `src/kindshot/config.py`
- `src/kindshot/guardrails.py`
- `src/kindshot/pipeline.py`
- `src/kindshot/replay.py`
- `tests/test_config.py`
- `tests/test_guardrails.py`
- `tests/test_pipeline.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Analysis window used: `2026-03-11`, `2026-03-12`, `2026-03-13`, `2026-03-16`, `2026-03-17`, `2026-03-18`, `2026-03-19` (latest 7 logged trading days available locally).
- Historical cohort evidence:
  - total BUYs: `23`
  - realized result: `11` wins / `12` losses, sum return `+0.150%`, approx `+7,487 KRW`
  - late `15m` cohort (`14:00+`): `5` trades, `0` wins, avg `-0.796%`, sum `-3.979%`, approx `-198,934 KRW`
  - what-if blocked late `15m` cohort: `18` trades, `61.1%` win rate, sum return `+4.128%`, approx `+206,421 KRW`
- Test commands:
  - `source .venv/bin/activate && python -m pytest tests/test_config.py tests/test_guardrails.py tests/test_pipeline.py -q` passed (`110 passed`)
  - `source .venv/bin/activate && python -m pytest -q` passed (`551 passed, 1 warning`)
- Diagnostics:
  - affected files in `src/` and `tests/` returned `0` LSP diagnostic errors

Risk and rollback note:
- The 7-day evidence window is limited by missing local logs after `2026-03-19`; the analysis explicitly uses the latest seven logged days rather than calendar-recent dates.
- Some historical late losers were also below today's confidence floor, so the new guardrail is additive risk control, not the sole explanation for those past losses.
- Roll back by reverting the fast-profile config fields and the `FAST_PROFILE_LATE_ENTRY` guardrail path in `guardrails.py`, `pipeline.py`, and `replay.py`.
