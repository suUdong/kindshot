# 2026-03-29 Deploy Readiness: Exit E2E and Deploy Script Validation

## Goal

Raise deployment confidence without touching `deploy/` by adding automated proof for the liquidation close path and for the existing deploy shell scripts.

## Problem

- The runtime has detailed unit coverage around snapshot scheduling and virtual exits, but deployment readiness still depends on manual trust that the final trade-close callback records the correct terminal metrics.
- The repository contains multiple operator-facing shell scripts under `deploy/`, but there is no automated test surface proving they still parse or that their safe informational branches still run.

## Scope

1. Add a bounded liquidation-path end-to-end regression test.
2. Add deploy-script validation coverage outside `deploy/`.
3. Preserve all runtime and deployment behavior.

## Non-Goals

- No edits under `deploy/`.
- No remote server mutation.
- No strategy or live-order changes.
- No new dependencies.

## Design

### Liquidation proof

- Prefer testing the real runtime trade-close handling over duplicating callback logic in test code.
- If the nested callback in `main.py` prevents direct testing, extract the side-effect block into a small helper with the same behavior and cover that helper directly.
- Assert against persisted performance artifacts, not just mock callback arguments.

### Deploy script proof

- Use subprocess-based tests from `tests/`.
- Validate every tracked deploy shell script with `bash -n`.
- For informational or verification-only branches, run scripts with stubbed `ssh`, `systemctl`, `curl`, and `journalctl` commands injected through `PATH`.
- Do not exercise mutating branches like `--apply` or rollback flows.

## Validation

- `pytest -q tests/test_exit_pipeline_e2e.py tests/test_deploy_scripts.py`
- `python3 -m compileall src tests`
- `pytest -q`

## Rollback

- Revert the commit.
- No deployment rollback is needed because this slice adds proof surfaces only.
