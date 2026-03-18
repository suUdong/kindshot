# Kindshot Codex Session State

## Current Session

- Branch: `main`
- Phase: `Historical Collection Foundation`
- Focus: Repeatable server-side historical backfill with no backlog blockers.
- Active hypothesis: with no-news-day backlog suppression and server swap enabled, auto backfill can keep progressing historical ranges without operator intervention beyond repeated invocation.

## Environment

- Host: local workspace
- Runtime target: Python `3.11+`
- Current local venv: `.venv` uses Python `3.12.3`
- Validation status: `source .venv/bin/activate && python -m pytest tests/test_collector.py -q` passed (`40 passed`) and `source .venv/bin/activate && python -m pytest -q` passes (`329 passed`).
- Tooling note: local `.venv` remains the default test runner path for subsequent batches.
- Git note: earlier local commits `0187bd5` and `ac26fe2` exist on `main`; `git push origin HEAD` still cannot reach `github.com` from this environment.
- Server ops note: `kindshot-server` now has `/swapfile` 2G enabled, persisted in `/etc/fstab`, with `vm.swappiness=10` and `vm.vfs_cache_pressure=50`.

## Last Completed Step

- Updated collector completeness rules so no-news trading days no longer remain blocked solely by `daily_index_missing`.
- Synced the collector fix to the server, confirmed backlog health, and continued auto backfill:
  - `scripts/collect_backfill_auto.py --max-days 3 --oldest-date 20260301` ended with `processed=0 complete=0 partial=0 skipped=2` for weekend dates `20260302->20260301`
  - `python -m kindshot collect status --json` now reports `health=healthy`, `partial_count=0`, `error_count=0`, `cursor_date=20260228`, `last_completed_date=20260303`
  - `scripts/collect_backfill_auto.py --max-days 5 --oldest-date 20260201` then completed `20260228->20260224` with `processed=4 complete=4 partial=0 skipped=1`, advancing cursor to `20260223`
- Registered a daily cron on `kindshot-server` for `02:40 KST` with `timeout 3h`, Telegram env injection, and log redirection to `/opt/kindshot/logs/backfill_auto.log`.

## Next Intended Step

- Let the scheduled cron continue historical catch-up nightly and watch Telegram plus `/opt/kindshot/logs/backfill_auto.log` for any reintroduced `partial` or `error` backlog before building replay-batch automation on top.

## Notes

- Keep the original UNKNOWN event immutable in logs; promotion remains a derived paper-only path.
- Keep live automation unchanged; `UNKNOWN_PAPER_PROMOTION_ENABLED` is still opt-in and only effective in paper mode.
- UNKNOWN rule queue output defaults to `data/unknown_review/rule_queue/latest.json`.
- UNKNOWN rule patch draft output defaults to `data/unknown_review/rule_patch/latest.json`.
- UNKNOWN article enrichment remains opt-in behind `UNKNOWN_REVIEW_ARTICLE_ENRICHMENT_ENABLED`.
- Recent narrow keyword adoption intentionally excluded ambiguous phrases like `협력 확대` and `주주환원 추진`.
- Recent IGNORE adoption still intentionally excludes broad summaries like `주요공시` pending a tighter false-positive review.
- Remaining risky administrative phrases like `증권 발행결과(자율공시)` are deferred until matching semantics can be tighter than simple substring.
- Server root still contains stray copies of `backfill_auto.py`, `collect_backfill_auto.py`, and `2026-03-16-backfill-automation.md`; the real paths under `/opt/kindshot/src`, `/opt/kindshot/scripts`, and `/opt/kindshot/docs/plans` are the ones in use.
