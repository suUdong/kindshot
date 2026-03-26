Hypothesis: The collector replay contract should use `data/collector/manifests/index.json` as the read entrypoint, not just the write index. If replay resolves per-day manifests via indexed `manifest_path` first and only then falls back to `YYYYMMDD.json`, replay/analysis stays aligned with the storage contract even when manifests are relocated or regenerated.

Changed files:
- `src/kindshot/replay.py`
- `tests/test_replay.py`
- `docs/plans/2026-03-13-data-collection-infra.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- `load_collected_day_manifest()` now resolves the manifest via indexed `manifest_path` when `index.json` has an entry for the date, and falls back to `data/collector/manifests/YYYYMMDD.json` for legacy datasets.
- Added replay coverage for:
  - index-first manifest resolution
  - legacy fallback when the index has no matching entry
- Updated the Phase 6 design doc to state the same read-path contract explicitly.
- `.venv/bin/python -m pytest tests/test_replay.py -q` passed (`32 passed`)
- `.venv/bin/python -m pytest -q` passed (`708 passed, 1 warning`)

Risk and rollback note:
- This slice changes replay's collector-manifest lookup only; it does not change collector writes, trading logic, or deployment paths.
- Legacy per-date manifest naming still works through fallback, so existing datasets remain readable.
- The confidence comparison is still evidence-blocked until a genuine post-upgrade runtime log is captured.
- Roll back by reverting `src/kindshot/replay.py`, `tests/test_replay.py`, the design-doc note, and the session summary updates.
