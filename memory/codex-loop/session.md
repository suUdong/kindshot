# Kindshot Codex Session State

## Current Session

- Branch: `main`
- Phase: `Historical Collection Foundation`
- Focus: expose collector partial-input reasons directly in replay status so operators can see why a collector day is incomplete without opening the raw manifest file.
- Active hypothesis: if replay day status/ops summaries include collector `status_reason`, `generated_at`, and the resolved `manifest_path`, partial collector inputs become actionable instead of just being labeled generic `COLLECTOR_PARTIAL_STATUS`.

## Environment

- Host: local workspace
- Runtime target: Python `3.11+`
- Current local venv: `.venv` uses Python `3.12.3`
- Validation status:
  - `.venv/bin/python -m pytest tests/test_replay.py -q` passed (`32 passed`)
  - `.venv/bin/python -m pytest -q` passed (`708 passed, 1 warning`)

## Last Completed Step

- Extended replay collector input summaries so replay day status now surfaces collector `status_reason`, `generated_at`, and the resolved `manifest_path`.
- Kept the manifest-index read contract in place and wired the resolved manifest path through the bundle summary.
- Added replay coverage proving partial-input status reports now expose the collector reason and source manifest path.
- Updated the collection-infra design doc so replay status/ops output expectations match the code.
- Verified the full repository test suite after the change (`708 passed, 1 warning`).

## Next Intended Step

- If a genuine post-upgrade runtime decision log appears, run `python3 scripts/confidence_report.py --log-file <before> --log-file <after>` and close the confidence-comparison evidence gap.
- Otherwise continue Phase 6 with the next replay-facing collector slice that improves operator/replay usability without needing new external runtime evidence.

## Notes

- This slice changes replay/collector read-path behavior only; strategy and live-order boundaries remain untouched.
- The confidence-comparison follow-up is still blocked on a genuine post-upgrade runtime log.
