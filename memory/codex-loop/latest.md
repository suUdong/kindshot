Hypothesis: If the runtime trade-close side effects are exercised end to end through `SnapshotScheduler` and the checked-in deploy shell scripts are validated through automated syntax/smoke tests, then Kindshot server deployment prep can rely on fresh local evidence instead of manual trust for the liquidation path and operator scripts.

Changed files:
- `src/kindshot/main.py`
- `tests/test_exit_pipeline_e2e.py`
- `tests/test_deploy_scripts.py`
- `docs/plans/2026-03-29-deploy-readiness-exit-e2e.md`
- `memory/codex-loop/latest.md`
- `.omx/context/deploy-readiness-exit-e2e-20260328T203750Z.md`
- `.omx/plans/prd-deploy-readiness-exit-e2e-20260329.md`
- `.omx/plans/test-spec-deploy-readiness-exit-e2e-20260329.md`

Implementation summary:
- Extracted the runtime trade-close side-effect block in `main.py` into `_handle_trade_close()` without changing behavior, then left the existing nested callback as a thin wrapper.
- Added `tests/test_exit_pipeline_e2e.py` to prove:
  - a paper take-profit exit is recorded once into performance artifacts through the real runtime helper
  - a partial-take-profit plus final trailing-stop path records only the final cumulative trade outcome once
- Added `tests/test_deploy_scripts.py` to validate:
  - every tracked `deploy/*.sh` passes `bash -n`
  - `deploy/logs.sh help`
  - `deploy/verify-live.sh --local`
  - `deploy/go-live.sh --verify`
  - `deploy/status.sh`
  all run successfully under stubbed commands with no `deploy/` edits
- Ran non-destructive remote checks against `kindshot-server` using the existing scripts to confirm the current server still reports healthy paper-mode service state.

Validation:
- `pytest -q tests/test_exit_pipeline_e2e.py tests/test_deploy_scripts.py` -> `7 passed`
- `python3 -m compileall src tests` -> success
- `pytest -q` -> `1087 passed, 1 skipped, 1 warning`
- diagnostics on changed files:
  - `src/kindshot/main.py` -> 0 errors
  - `tests/test_exit_pipeline_e2e.py` -> 0 errors
  - `tests/test_deploy_scripts.py` -> 0 errors
- remote non-destructive checks:
  - `ssh -o BatchMode=yes -o ConnectTimeout=5 kindshot-server "echo connected"` -> `connected`
  - `bash deploy/go-live.sh --verify` -> service `active`, mode `PAPER`, health responded
  - `ssh kindshot-server "cd /opt/kindshot && bash deploy/status.sh"` -> service `active`, health/journal visible, today's JSONL file absent

Simplifications made:
- Reused the production trade-close side effects via one extracted helper instead of duplicating callback logic in tests.
- Validated deploy scripts with subprocess stubs from `tests/` instead of introducing a separate validation framework or editing `deploy/`.
- Kept the deploy proof focused on safe informational/verification branches only.

Remaining risks:
- The deploy script smoke tests stub shell dependencies, so they do not prove every real remote environment assumption; they prove syntax and expected control flow for the covered branches.
- Remote status still shows no current-day JSONL runtime file, so deployment readiness is improved but live feed inactivity remains an operational observation gap rather than a code/test gap.
- `bash deploy/go-live.sh --verify` reports `MICRO_LIVE_MAX_ORDER_WON` and `TELEGRAM_BOT_TOKEN` unset on the current server, which matters for eventual live transition but did not block this paper-mode prep slice.

Rollback note:
- Revert the commit to remove the helper extraction and the new test/docs surfaces; no deploy/runtime rollback is otherwise required because this slice does not change `deploy/`, secrets, or live-order behavior.
