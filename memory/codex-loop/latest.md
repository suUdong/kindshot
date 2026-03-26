Hypothesis: A weekly operator report grounded in `deploy/daily_report.py` will show whether recent BUY performance is broad-based or concentrated in a narrow subset of buckets, and that view should be captured in a durable doc before choosing the next trading-rule slice.

Changed files:
- `docs/weekly-performance.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Targeted verification:
  - `source .venv/bin/activate && python -m pytest tests/test_daily_report.py tests/test_strategy_observability.py tests/test_strategy_comparison.py tests/test_hold_profile.py -q` passed (`19 passed`)
- Full verification:
  - `source .venv/bin/activate && python -m pytest -q` passed (`585 passed, 1 warning`)
- Diagnostics:
  - not applicable for docs-only edits
- Review:
  - local architect-style verification passed; subagent architect handoff was not used because this turn did not include an explicit user delegation request and the workspace has no `.codex/prompts/architect.md`

Risk and rollback note:
- This slice changes documentation and session summaries only; live execution behavior is unchanged.
- The weekly report reflects only the latest 7 logged trading days available locally, not the latest calendar 7 days.
- Roll back by reverting `docs/weekly-performance.md` and the matching `memory/codex-loop/*.md` summary updates.
