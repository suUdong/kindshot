Hypothesis: Replay ops console output should preserve the same collector blocker context that the row JSON already carries. If queue/run/cycle terminal output prints `collector_status_reason` and `collector_manifest_path`, operators can triage blocked replay days without opening saved JSON artifacts.

Changed files:
- `src/kindshot/replay.py`
- `tests/test_replay.py`
- `docs/plans/2026-03-13-data-collection-infra.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Replay ops queue/run/cycle console output now prints collector blocker details when present on a row.
- Kept the prior enriched row payloads intact; this slice only makes the human-readable terminal surface match the existing JSON data.
- Added replay coverage for:
  - queue console output exposing collector blocker details
  - run console output exposing collector blocker details
  - cycle console output exposing collector blocker details
- Updated the Phase 6 design doc to state the replay-ops console visibility contract explicitly.
- `.venv/bin/python -m pytest tests/test_replay.py -q` passed (`34 passed`)
- `.venv/bin/python -m pytest -q` passed (`710 passed, 1 warning`)

Risk and rollback note:
- This slice changes replay ops/output visibility only; it does not change collector writes, trading logic, or deployment paths.
- Existing replay consumers remain compatible because the enriched fields are additive.
- The confidence comparison is still evidence-blocked until a genuine post-upgrade runtime log is captured.
- Roll back by reverting `src/kindshot/replay.py`, `tests/test_replay.py`, the design-doc note, and the session summary updates.
