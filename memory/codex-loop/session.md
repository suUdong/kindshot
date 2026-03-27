# Kindshot Codex Session State

## Current Session

- Branch: `main`
- Phase: Post-Deployment Observation
- Focus: the avg20d volume-ratio entry-quality slice is now pushed and deployed; the immediate follow-up is observing the next live paper session for real runtime evidence.
- Active hypothesis: if the deployed avg20d volume-ratio guardrail and confidence path behaves as intended during the next market session, then weak-liquidity BUYs should be filtered earlier without disturbing the existing paper-mode runtime surfaces.
- Blocker: no deployment blocker remains; the only open evidence gap is fresh live-session behavior under market hours.

## Environment

- Host: local workspace + `kindshot-server`
- Runtime target: `/opt/kindshot`
- Validation status:
  - local compile passed (`python3 -m compileall src scripts tests dashboard`)
  - local entry-filter report wrote `logs/daily_analysis/entry_filter_analysis_20260328.txt`
  - local targeted pytest passed (`232 passed`)
  - local full pytest passed (`1013 passed, 1 skipped, 1 warning`)
  - diagnostics returned `0 errors`, `0 warnings`
  - pushed runtime commit: `709cfd7`
  - remote compile/install/restart completed
  - remote `kindshot` + `kindshot-dashboard` are both `active`
  - remote `/health` returned `healthy` with `started_at=2026-03-28T06:30:00.854671+09:00`
  - remote dashboard HTTP probe returned `HTTP/1.1 200 OK`

## Last Completed Step

- Committed the avg20d volume-ratio entry-quality slice as `709cfd7`, pushed `main`, re-synced `/opt/kindshot`, reinstalled it into the remote venv, restarted both services, and verified runtime health plus dashboard reachability.

## Next Intended Step

- On the next Korean market session, inspect runtime logs and `/health` to confirm the deployed volume-ratio path is producing the expected guardrail / confidence evidence.
- If the new signal coverage stays sparse, collect another bounded history window before tightening `min_volume_ratio_vs_avg20d` or `volume_ratio_surge_threshold`.

## Notes

- The local shell environment did not have the `ks` alias, so this deployment used direct `ssh kindshot-server` / `rsync ... kindshot-server:/opt/kindshot/...`.
- The remote `./.venv/bin/pip` wrapper had a stale `.venv.new` shebang; `./.venv/bin/python -m pip` worked and was used for this deploy.
- This run did not alter `deploy/`, secrets, `.env`, or live-order behavior.
