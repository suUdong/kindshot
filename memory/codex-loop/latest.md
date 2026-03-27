Hypothesis: If Kindshot turns raw headlines into shared semantic enrichment with numeric fact extraction, per-ticker related-news clustering, and bounded impact scoring, then paper-mode decisions can distinguish strong direct disclosures from weak commentary more reliably without changing deploy, secret, or live-order behavior.

Changed files:
- `docs/plans/2026-03-28-nlp-pipeline-upgrade.md`
- `src/kindshot/context_card.py`
- `src/kindshot/decision.py`
- `src/kindshot/models.py`
- `src/kindshot/news_semantics.py`
- `src/kindshot/pipeline.py`
- `src/kindshot/trade_db.py`
- `tests/test_decision.py`
- `tests/test_news_semantics.py`
- `tests/test_pipeline.py`
- `tests/test_trade_db.py`
- `DEPLOYMENT_LOG.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`
- `memory/codex-loop/roadmap.md`

Implementation summary:
- Added `src/kindshot/news_semantics.py` as a shared headline semantic enrichment layer for:
  - contract/revenue/operating-profit extraction
  - per-ticker related-news clustering
  - bounded impact scoring and small confidence shaping
- Extended `EventRecord` to persist `analysis_headline`, `news_category`, and nested `news_signal` metadata.
- Threaded `news_signal` into runtime context-card artifacts, decision prompt construction, pipeline confidence shaping, and trade DB backfill.
- Added additive trade DB fields plus lightweight migration support for `news_cluster_id`, `news_cluster_size`, `contract_amount_eok`, and `impact_score`.
- Added regression coverage for semantic extraction, clustering, prompt enrichment, pipeline logging, and DB backfill.
- Committed the runtime slice as `42c2333`, pushed `main`, re-synced `/opt/kindshot`, reinstalled the remote venv, restarted both services, and confirmed fresh runtime health.

Deployment evidence summary:
- Remote host: `kindshot-server` (`/opt/kindshot`)
- Deployed runtime commit: `42c2333`
- Services:
  - `systemctl is-active kindshot` → `active`
  - `systemctl is-active kindshot-dashboard` → `active`
- `systemctl status` showed:
  - `kindshot` active since `2026-03-28 07:51:57 KST`
  - `kindshot-dashboard` active since `2026-03-28 07:51:57 KST`
- Remote `/health` summary returned:
  - `status=healthy`
  - `started_at=2026-03-28T07:51:59.106745+09:00`
  - `last_poll_source=feed`
  - `last_poll_age_seconds=6`
  - `guardrail_state.configured_max_positions=4`
  - `guardrail_state.position_count=0`
  - `trade_metrics.total_trades=0`
  - `recent_pattern_profile.total_trades=14`
- Remote dashboard HTTP probe returned:
  - `HEAD http://127.0.0.1:8501/` → `200`
  - `Content-Type: text/html`

Validation:
- local `python3 -m compileall src tests scripts dashboard`
- local `.venv/bin/python -m pytest tests/test_news_semantics.py tests/test_decision.py tests/test_pipeline.py tests/test_trade_db.py tests/test_context_card.py -x -vv` → `140 passed`
- local `.venv/bin/python -m pytest -q` → `1028 passed, 1 skipped, 1 warning`
- diagnostics `lsp_diagnostics_directory` → `0 errors`, `0 warnings`
- `git push origin main` → `42c2333` pushed
- remote `rsync --delete src/` → `/opt/kindshot/src/`
- remote `rsync --delete tests/` → `/opt/kindshot/tests/`
- remote `rsync pyproject.toml README.md requirements.lock` → `/opt/kindshot/`
- remote `./.venv/bin/python -m compileall src/kindshot tests dashboard`
- remote `./.venv/bin/python -m pip install -e . --quiet`
- remote `sudo -n systemctl restart kindshot kindshot-dashboard`
- remote `systemctl is-active kindshot kindshot-dashboard` → both `active`
- remote `curl -fsS http://127.0.0.1:8080/health` → healthy JSON
- remote dashboard probe via Python `HEAD http://127.0.0.1:8501/` → `200 text/html`

Simplifications made:
- Kept clustering deterministic and in-memory instead of introducing embeddings or external services.
- Reused the existing direct-disclosure/commentary parser instead of creating a second headline classification layer.
- Limited impact-score confidence shaping to a small bounded adjustment so existing guardrails remain primary.

Remaining risks:
- Headline-only revenue/operating-profit parsing can still miss or misread rare phrasings that require body text.
- Cluster state is runtime-local, so corroboration does not survive process restarts in this slice.
- Real market-hours observation is still needed to confirm the new impact-score path improves paper-mode decision quality under live headline flow.
- Remote service restart currently requires `sudo -n systemctl ...`; plain `systemctl restart` hits polkit and is not sufficient for deploy automation.

Rollback note:
- Re-sync the prior known-good runtime tree to `/opt/kindshot`, rerun `./.venv/bin/python -m pip install -e . --quiet`, and restart `kindshot` plus `kindshot-dashboard`.
