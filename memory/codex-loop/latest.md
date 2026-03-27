Hypothesis: If Kindshot blocks stale BUYs once entry delay exceeds 60 seconds, rejects aggregate orderbook bid/ask imbalance, and tightens thin-liquidity admission using stronger participation plus prior-volume confirmation, then the paper runtime can avoid low-quality news entries without changing the LLM path or execution surface.

Changed files:
- `src/kindshot/config.py`
- `src/kindshot/entry_filter_analysis.py`
- `src/kindshot/pipeline.py`
- `src/kindshot/guardrails.py`
- `scripts/entry_filter_analysis.py`
- `tests/test_pipeline.py`
- `tests/test_guardrails.py`
- `tests/test_entry_filter_analysis.py`
- `docs/plans/2026-03-28-entry-filter-optimization.md`
- `.omx/plans/prd-entry-strategy-optimization-20260328.md`
- `.omx/plans/test-spec-entry-strategy-optimization-20260328.md`
- `.omx/context/entry-optimization-20260327T183637Z.md`
- `DEPLOYMENT_LOG.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`
- `memory/codex-loop/roadmap.md`

Implementation summary:
- Added a hard stale-entry guardrail (`ENTRY_DELAY_TOO_LATE`) using `max_entry_delay_ms=60000` on top of the existing soft delay confidence penalty.
- Added aggregate orderbook imbalance filtering (`ORDERBOOK_IMBALANCE`) using total bid depth vs total ask depth with a conservative `0.8` floor.
- Tightened liquidity admission by raising `min_intraday_value_vs_adv20d` to `0.15` and adding a `PRIOR_VOLUME_TOO_THIN` guardrail after `10:00 KST` when `prior_volume_rate < 70`.
- Added a shared entry-filter helper module plus a local `scripts/entry_filter_analysis.py` report that writes JSON/text evidence under `logs/daily_analysis/`.
- Deployed the entry-filter runtime slice with the existing clean-export `rsync` lane, then followed with the helper-module hotfix commit (`95c740d`) after the first remote restart exposed the missing import.

Validation:
- local `python3 -m compileall src scripts tests`
- local `.venv/bin/python -m pytest tests/test_guardrails.py tests/test_context_card.py tests/test_pipeline.py tests/test_entry_filter_analysis.py -q` → `213 passed`
- local `.venv/bin/python scripts/entry_filter_analysis.py` produced:
  - `delay<=60s`: `12` trades, avg `-0.073%`, win rate `33.3%`
  - `delay>60s`: `2` trades, avg `-0.602%`, win rate `0.0%`
  - `intraday_value_vs_adv20d>=0.15`: `5` trades, avg `+0.071%`, win rate `60.0%`
  - orderbook ratio coverage: `0` real runtime rows, so the `0.8` floor remains a conservative prior
- local `.venv/bin/python -m pytest -q` → `988 passed, 1 skipped, 1 warning`
- diagnostics `lsp_diagnostics_directory` → `0 errors`, `0 warnings`
- `git push origin main` → `Everything up-to-date`
- remote `./.venv/bin/python -m compileall src/kindshot scripts/entry_filter_analysis.py`
- remote `./.venv/bin/python -m pip install -e . --quiet`
- remote `systemctl is-active kindshot` → `active`
- remote `systemctl is-active kindshot-dashboard` → `active`
- remote `systemctl status kindshot --no-pager -l` → active since `2026-03-28 03:52:08 KST`, `ExecStart=/opt/kindshot/.venv/bin/python -m kindshot --paper`
- remote `journalctl -u kindshot -n 20 --no-pager` showed:
  - `kindshot 0.1.3 starting`
  - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=14 boost=1 loss=2`
  - `Health server started on 127.0.0.1:8080`
- remote `/health` summary:
  - `status: healthy`
  - `started_at: 2026-03-28T03:52:10.362041+09:00`
  - `last_poll_source: feed`
  - `last_poll_age_seconds: 10`
  - `guardrail_state.configured_max_positions: 4`
  - `guardrail_state.position_count: 0`

Simplifications made:
- Kept the entry optimization inside existing guardrails and pipeline wiring instead of adding scheduled delayed-entry machinery.
- Reused the existing intraday participation guardrail instead of adding a second overlapping liquidity subsystem.
- Reused one shared helper path for both runtime orderbook ratio math and offline entry-filter analysis.
- Reused the existing server deployment lane instead of adding new deploy tooling.

Remaining risks:
- The server is still in VTS pricing mode, so live orderbook/prior-volume quality is limited until real quote keys are restored.
- `2026-03-28` is a Saturday in KST, so the new BUY filters were validated through tests, offline analysis, and restart smoke checks, not a live market session.
- Real runtime orderbook-depth outcome coverage is still sparse, so the `0.8` imbalance floor should be revisited after fresh production-paper history accumulates.

Rollback note:
- Re-sync the prior known-good `src/kindshot/{config.py,guardrails.py,pipeline.py,entry_filter_analysis.py}` plus `scripts/entry_filter_analysis.py` to `/opt/kindshot`, reinstall with the remote venv, and restart `kindshot`.
