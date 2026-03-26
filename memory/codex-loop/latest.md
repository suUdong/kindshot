Hypothesis: Replay ops views should preserve the collector partial reason that replay day status already knows. If ops summary and ready-queue rows carry `collector_status_reason` and `collector_manifest_path`, multi-day replay triage stays actionable without opening per-day status artifacts.

Changed files:
- `src/kindshot/replay.py`
- `tests/test_replay.py`
- `docs/plans/2026-03-13-data-collection-infra.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Replay ops summary rows now preserve `collector_status_reason` and `collector_manifest_path` from the underlying day-status payload.
- Replay ready-queue rows now preserve the same fields, so excluded partial days still show their concrete blocker reason and source manifest.
- Kept the prior day-status and index-first manifest-resolution behavior intact; this slice only propagates existing signals upward into multi-day ops views.
- Added replay coverage for:
  - ops summary rows exposing `collector_status_reason`
  - ops summary rows exposing `collector_manifest_path`
  - queue-ready rows preserving the same partial collector fields
- Updated the Phase 6 design doc to state the replay-ops visibility contract explicitly.
- `.venv/bin/python -m pytest tests/test_replay.py -q` passed (`32 passed`)
- `.venv/bin/python -m pytest -q` passed (`708 passed, 1 warning`)

Risk and rollback note:
- This slice changes replay ops/output visibility only; it does not change collector writes, trading logic, or deployment paths.
- Existing replay consumers remain compatible because the enriched fields are additive.
- The confidence comparison is still evidence-blocked until a genuine post-upgrade runtime log is captured.
- Roll back by reverting `src/kindshot/replay.py`, `tests/test_replay.py`, the design-doc note, and the session summary updates.
