# Kindshot Codex Session State

## Current Session

- Branch: `main`
- Phase: `Historical Collection Foundation`
- Focus: keep the collector replay contract consistent by making replay resolve collector manifests through `data/collector/manifests/index.json` before falling back to legacy per-date paths.
- Active hypothesis: if replay trusts the collector manifest index as the read entrypoint, relocated or regenerated manifests will stay consumable without forcing every consumer to reconstruct the path convention.

## Environment

- Host: local workspace
- Runtime target: Python `3.11+`
- Current local venv: `.venv` uses Python `3.12.3`
- Validation status:
  - `.venv/bin/python -m pytest tests/test_replay.py -q` passed (`32 passed`)
  - `.venv/bin/python -m pytest -q` passed (`708 passed, 1 warning`)

## Last Completed Step

- Updated replay's collector-manifest read path to resolve `manifest_path` from `data/collector/manifests/index.json` before falling back to the legacy `YYYYMMDD.json` convention.
- Added replay coverage for both indexed manifest-path resolution and legacy fallback behavior.
- Updated the collection-infra design doc so the replay-facing storage contract explicitly says index lookup comes first.
- Verified the full repository test suite after the change (`708 passed, 1 warning`).

## Next Intended Step

- If a genuine post-upgrade runtime decision log appears, run `python3 scripts/confidence_report.py --log-file <before> --log-file <after>` and close the confidence-comparison evidence gap.
- Otherwise continue Phase 6 with the next replay-facing collector slice that does not require external runtime evidence.

## Notes

- This slice changes replay/collector read-path behavior only; strategy and live-order boundaries remain untouched.
- The confidence-comparison follow-up is still blocked on a genuine post-upgrade runtime log.
