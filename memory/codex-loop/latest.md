Hypothesis: If Kindshot records structured stage latency through the news→analysis→risk→order path, exposes recent profiling and cache stats via `/health`, and persists bounded LLM decision cache entries on disk, then operators can see real bottlenecks directly and avoid paying for equivalent prompt replays after restarts.

Changed files:
- `src/kindshot/config.py`
- `src/kindshot/context_card.py`
- `src/kindshot/decision.py`
- `src/kindshot/health.py`
- `src/kindshot/main.py`
- `src/kindshot/models.py`
- `src/kindshot/pipeline.py`
- `src/kindshot/runtime_latency.py`
- `scripts/runtime_latency_report.py`
- `tests/test_decision.py`
- `tests/test_health.py`
- `tests/test_pipeline.py`
- `docs/plans/2026-03-28-runtime-latency-and-llm-cache.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`
- `memory/codex-loop/roadmap.md`

Implementation summary:
- Added structured per-event runtime profiling with:
  - `news_to_pipeline_ms`
  - `context_card_ms`
  - `decision_total_ms`
  - `guardrail_ms`
  - `order_attempt_ms`
  - `pipeline_total_ms`
  - `llm_latency_ms`
  - `llm_cache_layer`
  - `bottleneck_stage`
- Persisted the profile inside event logs and surfaced recent aggregate summaries through `/health.latency_profile`.
- Extended `DecisionEngine` caching from memory-only TTL to bounded disk-backed cache under `data/runtime/llm_cache`, while preserving in-flight dedup.
- Added `DecisionEngine.cache_stats()` and surfaced it through `/health.llm_cache`.
- Tightened cache correctness by including market/risk/context inputs in the cache key while still bucketing `detected_at` to the minute for practical reuse.
- Reduced context-card bottleneck by parallelizing:
  - pykrx historical fetch
  - KIS price fetch
  - alpha-scanner fetch
- Added `scripts/runtime_latency_report.py` to summarize recent profiled runtime logs into:
  - `logs/daily_analysis/runtime_latency_report_20260328.json`
  - `logs/daily_analysis/runtime_latency_report_20260328.txt`

Latency evidence summary:
- Local profiling command currently reports `0` profiled historical events because existing checked-in logs (`20260325`~`20260327`) predate this instrumentation.
- Deployed `/health` now exposes:
  - `latency_profile` keys: `window_size`, `stages`, `bottlenecks`, `decision_sources`, `cache_layers`
  - `llm_cache` keys: `memory_entries`, `memory_hits`, `disk_hits`, `inflight_hits`, `misses`, `writes`, `disk_errors`

Validation:
- local `python3 -m compileall src scripts tests`
- local `.venv/bin/python -m pytest tests/test_decision.py tests/test_health.py tests/test_pipeline.py -q` → `101 passed`
- local `.venv/bin/python -m pytest -q` → `1001 passed, 1 skipped, 1 warning`
- local `.venv/bin/python scripts/runtime_latency_report.py`
- diagnostics `lsp_diagnostics_directory` → `0 errors`, `0 warnings`
- commit/push:
  - `git commit` → `23600c8`
  - `git push origin main` → `23600c8` pushed to `origin/main`
- remote deploy:
  - clean-export re-rsync from `HEAD` to avoid carrying unrelated dirty local files
  - remote `./.venv/bin/python -m compileall src/kindshot scripts/runtime_latency_report.py`
  - remote `./.venv/bin/python -m pip install -e . --quiet`
  - remote `systemctl restart kindshot kindshot-dashboard`
  - remote `systemctl is-active kindshot` → `active`
  - remote `systemctl is-active kindshot-dashboard` → `active`
  - remote `/health` summary:
    - `status=healthy`
    - `last_poll_source=feed`
    - `guardrail_state.configured_max_positions=4`
    - `latency_profile` block present
    - `llm_cache` block present
  - remote `systemctl status kindshot` showed active since `2026-03-28 04:23:07 KST`
  - remote journal showed the restarted paper runtime and health server startup

Simplifications made:
- Reused the existing event log record instead of adding a second profiling-only log stream.
- Kept the cache as bounded JSON files under the existing runtime data tree instead of adding a new storage dependency.
- Optimized only the obvious pre-LLM bottleneck (`build_context_card`) instead of rewriting the full pipeline scheduler.

Remaining risks:
- Existing historical logs do not contain the new `pipeline_profile` block, so local report evidence will remain empty until new runtime events are processed after deployment.
- The server is still in paper mode with VTS quote limitations; order-attempt latency samples will remain sparse or absent unless a live order path is explicitly exercised in a safe environment.
- `detected_at` is intentionally bucketed to the minute in the cache key; if future prompt behavior becomes more second-sensitive, this reuse policy should be re-validated before widening cache TTL or scope further.

Rollback note:
- Re-deploy the previous known-good commit to `/opt/kindshot` using the same clean-export `rsync` lane, then rerun `.venv` install and restart `kindshot` / `kindshot-dashboard`.
