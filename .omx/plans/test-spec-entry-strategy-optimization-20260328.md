# Test Spec: Entry Strategy Optimization

## Required Validation

1. Unit tests for the new delay and orderbook imbalance guardrails.
2. Unit tests for any new analysis helper logic.
3. Compile runtime, scripts, tests, and dashboard code.
4. Run the new entry-filter analysis command on local history and inspect the output.
5. Run targeted pytest for `guardrails`, `pipeline`, and the new analysis tests.
6. Run the full local test suite.
7. Run changed-file diagnostics.
8. Push and deploy, then verify remote services remain healthy.

## Targeted Commands

```bash
python3 -m compileall src scripts tests dashboard
.venv/bin/python -m pytest tests/test_guardrails.py tests/test_pipeline.py tests/test_entry_filter_analysis.py -q
.venv/bin/python -m pytest -q
```

## Analysis Command

```bash
.venv/bin/python scripts/entry_filter_analysis.py
```

The analysis output must show:

- delay cohort performance summary,
- the selected max-delay recommendation,
- current orderbook-ratio coverage or an explicit low-coverage warning,
- current intraday participation floor recommendation.

## Diagnostics

- Changed-file diagnostics show `0 errors`.

## Remote Verification

- Push to `origin/main`.
- Deploy to `kindshot-server:/opt/kindshot` using the existing `rsync` path.
- Verify at minimum:
  - remote compile/install for changed runtime files,
  - `systemctl is-active kindshot`,
  - `systemctl is-active kindshot-dashboard`,
  - `curl -fsS http://127.0.0.1:8080/health`.
