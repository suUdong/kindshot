Hypothesis: Kindshot still lacks a reusable one-file post-hoc analysis surface for trading logs. A standalone report that summarizes structured BUY/SKIP totals, decision-source split, inline BUY appetite, BUY-side guardrail blockers, hourly source distribution, and top skip reasons should make ad hoc NVIDIA-day style investigations fast and repeatable.

Changed files:
- `scripts/trading_log_report.py`
- `tests/test_trading_log_report.py`
- `docs/plans/2026-03-27-trading-log-analysis-report.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Added `scripts/trading_log_report.py` to summarize:
  - file metadata and record counts
  - structured BUY/SKIP totals
  - `decision_source` split
  - inline BUY/SKIP counts from `event.decision_action`
  - BUY-side guardrail blockers
  - hourly source distribution
  - top repeated skip reasons by source
- Added parser/render coverage in `tests/test_trading_log_report.py`
- `git diff --check` passed
- `python3 -m py_compile scripts/trading_log_report.py` passed
- Fixed the hourly distribution to normalize UTC / `Z` timestamps into KST before bucketing
- `source .venv/bin/activate && python -m pytest tests/test_trading_log_report.py tests/test_daily_report.py tests/test_strategy_observability.py -q` passed (`8 passed`)
- `python3 scripts/trading_log_report.py --log-file /tmp/kindshot-nvidia-day1/kindshot_20260326.jsonl` rendered the expected KST-aligned full-day defensive report (`0 BUY / 51 SKIP`, source split `LLM=30 RULE_FALLBACK=14 RULE_PREFLIGHT=7`, hours `11-20 KST`)

Risk and rollback note:
- This slice changes only analysis tooling and documentation; it does not change strategy, execution, or deployment wiring.
- The report is intentionally standalone under `scripts/` and does not alter `deploy/`.
- Roll back by reverting `scripts/trading_log_report.py`, the new tests/doc change, and restoring the previous `memory/codex-loop/latest.md` / `memory/codex-loop/session.md`.
