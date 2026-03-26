Hypothesis: Replay ops summary should be a real operator surface, not just a JSON writer with a blank printer. If the summary printer shows aggregate counts and the same collector blocker details already present on row payloads, multi-day readiness triage becomes possible directly from the terminal.

Changed files:
- `src/kindshot/replay.py`
- `tests/test_replay.py`
- `docs/plans/2026-03-13-data-collection-infra.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- `_print_replay_ops_summary()` now prints aggregate counts and per-row readiness details instead of only a header.
- Replay ops summary console output now includes collector blocker details when present on a row.
- Kept the prior enriched row payloads intact; this slice only makes the human-readable summary surface match the existing JSON data.
- Added replay coverage for:
  - summary console output exposing aggregate counts and collector blocker details
  - queue console output exposing collector blocker details
  - run console output exposing collector blocker details
  - cycle console output exposing collector blocker details
- Updated the Phase 6 design doc to state the replay-ops summary console contract explicitly.
- `.venv/bin/python -m pytest tests/test_replay.py -q` passed (`35 passed`)
- `.venv/bin/python -m pytest -q` passed (`711 passed, 1 warning`)

Risk and rollback note:
- This slice changes replay ops/output visibility only; it does not change collector writes, trading logic, or deployment paths.
- Existing replay consumers remain compatible because the enriched fields are additive.
- The confidence comparison is still evidence-blocked until a genuine post-upgrade runtime log is captured.
- Roll back by reverting `src/kindshot/replay.py`, `tests/test_replay.py`, the design-doc note, and the session summary updates.
