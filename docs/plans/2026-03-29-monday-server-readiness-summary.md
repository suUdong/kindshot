# 2026-03-29 Monday Server Readiness Summary

## Goal

Make Monday, 2026-03-30 KST server readiness check possible from a single read-only operator path outside `deploy/`, and align the checklist with the verified date/procedure.

## Problem

- `docs/monday-go-live-checklist.md` currently labels `2026-03-31` as Monday even though Monday is `2026-03-30`.
- `scripts/server_monitor.py` summarizes runtime/poll/journal state but not service activity or `/health` readiness.
- `deploy/go-live.sh --verify` is for local-to-remote usage, so invoking it on the remote host is misleading and fails by trying to SSH again.

## Scope

1. Extend `scripts/server_monitor.py` so it also summarizes:
   - `kindshot` service state and inferred mode from the active command line
   - `kindshot-dashboard` service state
   - local `/health` payload highlights
2. Keep the implementation read-only and standalone outside `deploy/`.
3. Update the Monday checklist so:
   - the target date is corrected to `2026-03-30 (월)`
   - the recommended verification path uses `scripts/server_monitor.py` and the correct `verify-live.sh` usage

## Non-Goals

- No `deploy/` edits
- No service restarts
- No `.env` or secret changes
- No runtime strategy or order logic changes

## Design

### Server monitor

- Add stdlib-only helpers to fetch:
  - service state via `systemctl`
  - process command line via `ps`
  - `/health` JSON via HTTP
- Keep failure handling soft: missing privileges or unavailable endpoints should degrade the summary, not crash the script.
- Render service + health sections before runtime/poll/journal sections so an operator sees readiness first.

### Checklist

- Correct the absolute Monday date.
- Prefer:
  - local shell: `bash deploy/verify-live.sh`
  - remote shell: `bash deploy/verify-live.sh --local`
  - remote/local summary: `python3 scripts/server_monitor.py YYYYMMDD`
- Preserve existing paper/live rollout notes.

## Validation

- `pytest tests/test_server_monitor.py -q`
- `python3 -m compileall scripts tests`
- affected-file diagnostics on `scripts/server_monitor.py` and `tests/test_server_monitor.py`

## Rollback

- Revert `scripts/server_monitor.py`
- Revert `tests/test_server_monitor.py`
- Revert `docs/monday-go-live-checklist.md`
- Revert this plan doc and Ralph planning artifacts
