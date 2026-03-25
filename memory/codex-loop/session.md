# Kindshot Codex Session State

## Current Session

- Branch: `main`
- Phase: `Daily Runtime Verification`
- Focus: explain zero-BUY pressure on 2026-03-25 and remove the dominant low-risk choke point.
- Active hypothesis: `ADV_THRESHOLD=50억` is too strict for `POS_STRONG`; reducing ADV only for strong-catalyst events should restore candidate flow without broadening `POS_WEAK` risk.

## Environment

- Host: local workspace
- Runtime target: Python `3.11+`
- Current local venv: `.venv` uses Python `3.12.3`
- Validation status: `source .venv/bin/activate && python -m pytest tests/test_collector.py -q` passed (`40 passed`) and `source .venv/bin/activate && python -m pytest -q` passes (`329 passed`).
- Tooling note: local `.venv` remains the default test runner path for subsequent batches.
- Git note: earlier local commits `0187bd5` and `ac26fe2` exist on `main`; `git push origin HEAD` still cannot reach `github.com` from this environment.
- Server ops note: `kindshot-server` now has `/swapfile` 2G enabled, persisted in `/etc/fstab`, with `vm.swappiness=10` and `vm.vfs_cache_pressure=50`.

## Last Completed Step

- Audited local runtime artifacts for `2026-03-25` and confirmed that today's `context_cards` / `price_snapshots` are test-generated while `logs/kindshot_20260325.jsonl` is missing.
- Reconstructed the latest real runtime day (`2026-03-19`) from local logs:
  - `232` events total
  - `66` `POS_STRONG`, `29` `POS_WEAK`
  - `21` LLM decisions with `BUY=2`, `SKIP=19`
  - `ADV_TOO_LOW=45`
  - `CONSECUTIVE_STOP_LOSS=0`
- Implemented a bounded strategy change:
  - added `POS_STRONG_ADV_THRESHOLD` support with effective bucket-level ADV resolution
  - applied the relaxed ADV floor only to `POS_STRONG` in both quant and final guardrail checks
  - left `POS_WEAK` and other paths on the stricter general ADV threshold
- Added strategy observability to operator outputs:
  - `deploy/daily_report.py` now prints a strategy activity section
  - Telegram daily summary now includes strategy counts
  - current local 7-log aggregate shows `TP=2`, `Trailing Stop=2`, `Max Hold=4`, `Hold Profile Applied=18`, `Kill Switch Halt=0`, `Close Cutoff=7`
- Wrote the operator report at `docs/daily-check-20260325.md`.

## Next Intended Step

- Sync or inspect the real `2026-03-25` runtime log from the running environment so the daily report can be cross-checked against non-local evidence, then watch whether the `POS_STRONG` ADV relaxation lifts BUY candidate flow without a large rise in false positives.

## Notes

- Local workspace currently lacks operational `2026-03-25` JSONL logs; do not treat `data/runtime/context_cards/20260325.jsonl` or `data/runtime/price_snapshots/20260325.jsonl` as production evidence because they are test fixtures.
- Kill switch is not the leading suspect in local evidence; no `CONSECUTIVE_STOP_LOSS` hit appears in recent real logs.
- The new ADV relaxation is deliberately scoped to `POS_STRONG` only to avoid broadening `POS_WEAK` quality.
