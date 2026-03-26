Hypothesis: Replay status should expose why collector input is partial, not just that it is partial. If replay day status/ops summaries include the collector manifest `status_reason`, `generated_at`, and resolved `manifest_path`, operators can diagnose collector backlog causes from the status artifact alone.

Changed files:
- `src/kindshot/replay.py`
- `tests/test_replay.py`
- `docs/plans/2026-03-13-data-collection-infra.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Replay collector summaries now include manifest `status_reason`, `generated_at`, and the resolved `manifest_path`, so `replay_day_status()` carries the partial-input cause directly in its JSON output.
- Kept the prior index-first manifest resolution behavior intact; the status summary uses that same resolved path.
- Added replay coverage for:
  - partial-input status exposing `status_reason`
  - partial-input status exposing the resolved `manifest_path`
  - legacy replay behavior remaining green under the enriched summary shape
- Updated the Phase 6 design doc to state the replay-status visibility contract explicitly.
- `.venv/bin/python -m pytest tests/test_replay.py -q` passed (`32 passed`)
- `.venv/bin/python -m pytest -q` passed (`708 passed, 1 warning`)

Risk and rollback note:
- This slice changes replay status/output visibility only; it does not change collector writes, trading logic, or deployment paths.
- Existing replay consumers remain compatible because the enriched fields are additive.
- The confidence comparison is still evidence-blocked until a genuine post-upgrade runtime log is captured.
- Roll back by reverting `src/kindshot/replay.py`, `tests/test_replay.py`, the design-doc note, and the session summary updates.
