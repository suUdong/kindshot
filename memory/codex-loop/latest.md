Hypothesis: no-news trading day를 `daily_index_missing` backlog에서 제외하고 서버 메모리 압박을 swap으로 완화하면, auto backfill을 반복 실행해도 historical backlog를 안정적으로 계속 밀 수 있다.

Changed files:
- `docs/plans/2026-03-13-data-collection-infra.md`
- `src/kindshot/collector.py`
- `tests/test_collector.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- `source .venv/bin/activate && python -m pytest tests/test_collector.py -q` passed (`40 passed`).
- `source .venv/bin/activate && python -m pytest -q` passed (`329 passed`).
- Server memory check confirmed `swapfile 2G` active with `swappiness=10`, `vfs_cache_pressure=50`.
- Server auto backfill: `python scripts/collect_backfill_auto.py --max-days 3 --oldest-date 20260301` returned `processed=0 complete=0 partial=0 skipped=2` for `20260302->20260301`, with both dates correctly marked `non_trading_day`.
- Server status after rerun: `health=healthy`, `partial_count=0`, `error_count=0`, `cursor_date=20260228`, `last_completed_date=20260303`.
- Server auto backfill expansion: `python scripts/collect_backfill_auto.py --max-days 5 --oldest-date 20260201` returned `processed=4 complete=4 partial=0 skipped=1` for `20260228->20260224`, advancing cursor to `20260223`.
- Server cron registered successfully and `cron` service is active:
  - `40 2 * * * cd /opt/kindshot && . /opt/kindshot/.venv/bin/activate && TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... /usr/bin/timeout 3h python scripts/collect_backfill_auto.py --max-days 5 --oldest-date 20260201 >> /opt/kindshot/logs/backfill_auto.log 2>&1`

Risk and rollback note:
- Risk is now concentrated in long-running server throughput rather than collector backlog semantics; the server is still a small Lightsail instance, so larger windows should continue to use modest `--max-days` values even with swap enabled.
- Old stray copies at `/opt/kindshot/backfill_auto.py`, `/opt/kindshot/collect_backfill_auto.py`, and `/opt/kindshot/2026-03-16-backfill-automation.md` remain on the server root and can confuse ad hoc operator checks if invoked by mistake.
- Roll back by removing the cron line with `crontab -e` or reinstalling a filtered crontab, and by reverting `docs/plans/2026-03-13-data-collection-infra.md`, `src/kindshot/collector.py`, `tests/test_collector.py`, `memory/codex-loop/latest.md`, and `memory/codex-loop/session.md`, then re-syncing the reverted collector file to the server if needed.
