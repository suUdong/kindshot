Hypothesis: If the dashboard flattens and surfaces deployed `news_signal` metadata where operators already inspect signals, then the next live paper session can validate numeric extraction, cluster corroboration, and impact-score behavior without spelunking raw JSONL files.

Changed files:
- `docs/plans/2026-03-28-news-signal-observability.md`
- `dashboard/app.py`
- `dashboard/data_loader.py`
- `tests/test_dashboard.py`
- `DEPLOYMENT_LOG.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Implementation summary:
- Flattened nested `news_signal` metadata in dashboard reader paths so events, context cards, and live-feed rows now expose `impact_score`, extracted amounts, cluster size, and direct-disclosure/article flags as first-class columns.
- Added a new `뉴스 시맨틱 신호` section to the signal-status dashboard tab with summary metrics plus a top-impact detail table for operator review.
- Added dashboard regression coverage for flattened `news_signal` fields in `load_events`, `load_context_cards`, and `load_live_feed`.
- Committed the dashboard slice as `d75c540`, pushed `main`, re-synced `/opt/kindshot/dashboard`, restarted only `kindshot-dashboard`, and confirmed fresh HTTP reachability.

Deployment evidence summary:
- Remote host: `kindshot-server` (`/opt/kindshot`)
- Deployed dashboard commit: `d75c540`
- Services:
  - `systemctl is-active kindshot-dashboard` → `active`
- `systemctl status` showed:
  - `kindshot-dashboard` active since `2026-03-28 08:15:47 KST`
- Remote dashboard HTTP probe returned:
  - `HEAD http://127.0.0.1:8501/` → `200`
  - `Content-Type: text/html`

Validation:
- local `python3 -m compileall dashboard tests/test_dashboard.py`
- local `.venv/bin/python -m pytest tests/test_dashboard.py -q` → `22 passed`
- `git push origin main` → `d75c540` pushed
- remote `rsync --delete dashboard/` → `/opt/kindshot/dashboard/`
- remote `rsync tests/test_dashboard.py` → `/opt/kindshot/tests/`
- remote `./.venv/bin/python -m compileall dashboard tests/test_dashboard.py`
- remote `sudo -n systemctl restart kindshot-dashboard`
- remote `systemctl is-active kindshot-dashboard` → `active`
- remote dashboard probe via Python `HEAD http://127.0.0.1:8501/` → `200 text/html`

Simplifications made:
- Kept the new observability entirely in the dashboard reader layer instead of adding a separate report CLI or changing runtime producers.
- Reused existing event/context-card artifacts as the source of truth for semantic display.

Remaining risks:
- The dashboard only reveals what runtime already records; live market-hours evidence is still needed to confirm `news_signal` values look sane on real headlines.
- Remote dashboard restart still requires `sudo -n systemctl ...`; plain `systemctl restart` is not sufficient for deploy automation.

Rollback note:
- Re-sync the prior known-good `dashboard/` tree to `/opt/kindshot/dashboard/` and restart `kindshot-dashboard`.
