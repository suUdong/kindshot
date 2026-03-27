Hypothesis: If Kindshot adds bad-news immediate liquidation, deterministic support-breach liquidation, and target-hit 50% partial take profit with trailing remainder, then the paper runtime can cut degrading positions faster while preserving the existing scheduler, bookkeeping, and deployment surfaces.

Changed files:
- `src/kindshot/config.py`
- `src/kindshot/context_card.py`
- `src/kindshot/models.py`
- `src/kindshot/pipeline.py`
- `src/kindshot/price.py`
- `tests/test_config.py`
- `tests/test_context_card.py`
- `tests/test_pipeline.py`
- `tests/test_price.py`
- `docs/plans/2026-03-28-exit-strategy-optimization.md`
- `DEPLOYMENT_LOG.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`
- `memory/codex-loop/roadmap.md`
- `.omx/context/exit-strategy-upgrade-20260327T181945Z.md`

Implementation summary:
- Added scheduler-managed forced liquidation for same-ticker `NEG_STRONG` and correction/withdrawal follow-up events without creating a second SELL decision pipeline.
- Added deterministic support references to context-card output and passed them into buy-side scheduler state so later snapshots can exit with `support_breach`.
- Split the support composite into a recent 5-session low and the older portion of the 20-session window so the medium-term floor remains live.
- Changed partial take-profit semantics so paper mode now realizes 50% when the configured target ratio is hit; the shipped default is `1.0`, so the default behavior is target-hit partial followed by tighter trailing.
- Reused the existing `kindshot-server:/opt/kindshot` `rsync` + remote venv reinstall + systemd restart deployment lane.
- Followed with a truthfulness fix commit (`f1f583d`) so the support composite and `PARTIAL_TAKE_PROFIT_TARGET_RATIO` config matched the actual runtime behavior.

Validation:
- local `python3 -m compileall src tests`
- local `.venv/bin/python -m pytest tests/test_config.py tests/test_context_card.py tests/test_price.py tests/test_pipeline.py tests/test_performance.py tests/test_telegram_ops.py -q` → `130 passed, 1 skipped`
- local `.venv/bin/python -m pytest -q` → `981 passed, 1 skipped, 1 warning`
- diagnostics `lsp_diagnostics_directory` → `0 errors`, `0 warnings`
- remote `./.venv/bin/python -m compileall src/kindshot`
- remote `./.venv/bin/python -m pip install -e . --quiet`
- remote `systemctl is-active kindshot` → `active`
- remote `systemctl status kindshot --no-pager -l` → active since `2026-03-28 03:37:57 KST`, `ExecStart=/opt/kindshot/.venv/bin/python -m kindshot --paper`
- remote `journalctl -u kindshot -n 20 --no-pager` showed:
  - `kindshot 0.1.3 starting`
  - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=14 boost=1 loss=2`
  - `Health server started on 127.0.0.1:8080`
- remote `/health` summary:
  - `status: healthy`
  - `started_at: 2026-03-28T03:37:59.058179+09:00`
  - `last_poll_source: feed`
  - `last_poll_age_seconds: 9`
  - `guardrail_state.configured_max_positions: 4`
  - `guardrail_state.position_count: 0`

Simplifications made:
- Kept all new exit behavior inside `SnapshotScheduler` and `pipeline` instead of adding a separate SELL decision/action layer.
- Reused existing partial/final trade-close payloads, Telegram formatting, and performance bookkeeping paths.
- Reused the existing server deployment lane instead of adding new deploy tooling.

Remaining risks:
- The server is still in VTS pricing mode, so real-time price quality for support/trailing/T5M validation remains limited until real quote keys are configured.
- `2026-03-28` is a Saturday in KST, so the new exit reasons could only be validated through tests and service smoke checks, not live same-session market behavior.
- Forced liquidation routing assumes same-ticker positions remain scheduler-unique; widening same-ticker multi-position behavior would require revisiting that routing.

Rollback note:
- Re-sync the prior known-good `src/kindshot/{config.py,context_card.py,models.py,pipeline.py,price.py}` slice to `/opt/kindshot/src/`, reinstall with the remote venv, and restart `kindshot`.
