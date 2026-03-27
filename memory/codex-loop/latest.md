Hypothesis: If the current runtime prompt/risk baseline is verified and deployed as-is, Kindshot can ship the requested v69 behavior set without reopening the implementation surface.

Changed files:
- `DEPLOYMENT_LOG.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Implementation summary:
- Confirmed the current `main` / `origin/main` head (`846cfd5`) already contains the requested runtime features:
  - structured `ctx_signal` / `ctx_risk` prompt inputs and updated decision prompt guidance
  - partial take-profit bookkeeping with post-partial trailing behavior
  - dynamic daily loss floor calculation surfaced in guardrails and health
- Ran local compile + targeted pytest + full pytest against the existing implementation.
- Deployed the current head to `kindshot-server` via clean export `rsync`, reinstalled the package, restarted `kindshot` and `kindshot-dashboard`, and verified the runtime health/dashboard endpoints.

Validation:
- `python3 -m compileall src/kindshot tests scripts`
- `.venv/bin/python -m pytest tests/test_config.py tests/test_decision.py tests/test_guardrails.py tests/test_price.py tests/test_telegram_ops.py -q`
- `.venv/bin/python -m pytest tests/test_pipeline.py tests/test_performance.py tests/test_main_cli.py tests/test_health.py -q`
- `.venv/bin/python -m pytest -q`
- remote `python3 -m compileall src/kindshot scripts tests`
- remote `source .venv/bin/activate && python -m pip install . --quiet`
- remote `systemctl is-active kindshot kindshot-dashboard` → `active`, `active`
- remote `curl http://127.0.0.1:8080/health` → `healthy`
- remote `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
- Result: local `934 passed, 1 skipped, 1 warning`

Simplifications made:
- Treated the already-committed runtime implementation as the release candidate instead of forcing an extra code diff.
- Reused the existing clean-export deployment path rather than touching remote git state or `deploy/`.

Remaining risks:
- The remote service is still in VTS mode for price snapshots until real quote keys are present, so post-deploy live observation should focus on prompt/guardrail behavior more than snapshot realism.
- The new partial-exit and dynamic-loss behavior was validated locally and by remote startup/health checks, but its production-day calibration still depends on the next live/paper session.

Rollback note:
- Re-sync the prior known-good tree to `/opt/kindshot`, reinstall with the remote venv, and restart `kindshot` + `kindshot-dashboard`.
