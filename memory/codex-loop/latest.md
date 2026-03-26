Hypothesis: If `kindshot collect status` reads manifest index/day manifests while building blocked backlog details, operators can understand partial/error collector dates from one status surface without opening raw manifest files separately.

Changed files:
- `src/kindshot/collector.py`
- `tests/test_collector.py`
- `docs/plans/2026-03-27-collector-status-manifest-awareness.md`
- `.omx/context/collector-status-manifest-awareness-20260326T230615Z.md`
- `.omx/plans/prd-collector-status-manifest-awareness-20260327.md`
- `.omx/plans/test-spec-collector-status-manifest-awareness-20260327.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- `collect status` backlog detail rows now carry manifest-aware context (`manifest_path`, existence, status, status_reason, generated_at`) while preserving the prior summary and detail fields.
- Human-readable `collect status` logs now print manifest status/path context for blocked partial/error rows instead of forcing a second manifest lookup.
- Added collector regression coverage for the new status-detail helper, stale manifest-index fallback, manifest-aware JSON payloads, and log output.
- `python3 -m compileall src/kindshot` passed.
- `.venv/bin/python -m pytest tests/test_collector.py -q` passed (`44 passed`)
- LSP diagnostics on `src/kindshot/collector.py` and `tests/test_collector.py` returned `0` errors.
- `.venv/bin/python -m pytest -q` passed (`739 passed, 1 warning`)

Risk and rollback note:
- This slice changes collector status read paths only; it does not change backfill write semantics, replay execution, deployment paths, or live-order boundaries.
- Error backlog rows may legitimately have no manifest yet when backfill failed before manifest write; the new fields expose that absence rather than inventing state.
- Roll back by reverting `src/kindshot/collector.py`, `tests/test_collector.py`, the design/plan artifacts, and the session summary updates.
