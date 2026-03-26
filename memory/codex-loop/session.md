# Kindshot Codex Session State

## Current Session

- Branch: `main`
- Phase: `Weekly Performance Review`
- Focus: publish a fresh weekly report at `docs/weekly-perf.md` that uses only verifiable real trading logs and explicitly excludes polluted runtime test fixtures.
- Active hypothesis: the latest verifiable BUY performance is narrow, with most bucket-level edge still negative even inside the `POS_STRONG` surface.

## Environment

- Host: local workspace
- Runtime target: Python `3.11+`
- Current local venv: `.venv` uses Python `3.12.3`
- Validation status:
  - `source .venv/bin/activate && python - <<'PY' ... PY` recomputed the report from `logs/kindshot_20260311.jsonl` through `logs/kindshot_20260319.jsonl`
  - `source .venv/bin/activate && python - <<'PY' ... PY` confirmed `data/runtime/*/20260322-20260326.jsonl` are synthetic fixture outputs (`run_id=test_run`, synthetic `event_id`)

## Last Completed Step

- Recomputed the latest 7 real logged trading days directly from local operator logs using `classify_buy_exit()` reconstruction.
- Wrote `docs/weekly-perf.md` with coverage caveats, daily realized returns, runtime bucket returns, keyword bucket returns, and exit bucket returns.
- Recorded that only `16` of `23` BUY decisions currently have reconstructable realized returns and that `20260322` to `20260326` runtime artifacts are test-fixture pollution.

## Next Intended Step

- Re-run the report after real runtime logs are synced beyond `2026-03-19`.
- Use the refreshed bucket evidence to choose the next bounded trading-rule hypothesis, likely starting from the still-negative `수주` or `공급계약` cohorts.

## Notes

- This slice stays in the analysis/documentation layer; live execution logic was not changed here.
- The working tree still contains unrelated pre-existing untracked paths that were not part of this slice.
