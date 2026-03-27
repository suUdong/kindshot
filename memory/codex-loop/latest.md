Hypothesis: If runtime alerts are promoted from partial BUY-only messaging to full trade-close, guardrail-block, and end-of-day summary notifications, operators can monitor Kindshot paper trading from Telegram without reopening logs or the dashboard during routine operation.

Changed files:
- `docs/plans/2026-03-27-alerting-system-hardening.md`
- `src/kindshot/main.py`
- `src/kindshot/performance.py`
- `src/kindshot/price.py`
- `src/kindshot/telegram_ops.py`
- `tests/test_performance.py`
- `tests/test_price.py`
- `tests/test_telegram_ops.py`
- `memory/codex-loop/latest.md`

Implementation summary:
- Added Telegram `SELL`/trade-close notifications with exit type, horizon, return, P&L, and remaining position count.
- Expanded guardrail block alerts to show all blocked BUYs and whether `shadow` tracking was scheduled.
- Connected `PerformanceTracker` to actual trade-close callbacks so paper-mode virtual exits now update daily performance and position state at exit time instead of waiting for close.
- Added a once-per-day Telegram summary notifier that sends win rate, realized P&L, guardrail P&L, and open-position state after the close snapshot window.
- Hardened `PerformanceTracker` day rollover so long-running runtimes can advance dates cleanly even before the next trade arrives.

Validation:
- `python3 -m compileall src/kindshot tests scripts`
- `.venv/bin/python -m pytest tests/test_telegram_ops.py tests/test_price.py tests/test_performance.py -q`
- `.venv/bin/python -m pytest -q`
- Result: `877 passed, 1 skipped, 1 warning`

Simplifications made:
- Reused the existing stdlib Telegram client instead of introducing a new notifier dependency.
- Kept daily-summary dedupe state in the existing runtime state directory as a single JSON file.
- Reused `PerformanceTracker` rather than adding a second closeout aggregation path.

Remaining risks:
- Daily summary delivery still depends on the runtime staying alive past the close snapshot delay window and on Telegram credentials being configured.
- Paper-mode position accounting now closes at virtual exit time, which is more faithful to strategy behavior, but any downstream consumer that assumed close-only realization should be rechecked in production logs after deploy.

Rollback note:
- Revert the alerting changes in `main.py`, `performance.py`, `price.py`, `telegram_ops.py`, and related tests to restore the previous BUY-only notification behavior.
