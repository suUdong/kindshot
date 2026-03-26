Hypothesis: A fresh weekly operator report should separate real trading evidence from polluted runtime fixtures, then show whether the latest verifiable BUY performance is broad-based or trapped in a few negative keyword buckets.

Changed files:
- `docs/weekly-perf.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Analysis verification:
  - `source .venv/bin/activate && python - <<'PY' ... PY` recomputed the report directly from `logs/kindshot_20260311.jsonl`, `20260312`, `20260313`, `20260316`, `20260317`, `20260318`, and `20260319` using `classify_buy_exit()` from `src/kindshot/strategy_observability.py`
  - Result matched the expected aggregate: `23` BUY decisions, `16` reconstructable realized trades, win rate `31.2%`, realized-return sum `-0.141%`
- Fixture-pollution check:
  - `source .venv/bin/activate && python - <<'PY' ... PY` confirmed `data/runtime/context_cards/20260322-20260326.jsonl` are all `run_id=test_run` with one synthetic `event_id`
  - The same check confirmed `data/runtime/price_snapshots/20260322-20260326.jsonl` are all `run_id=run1` with one synthetic `event_id=evt1`
- Tests:
  - not run because this slice is docs-only and did not change executable code

Risk and rollback note:
- This slice changes documentation and session summaries only; trading behavior is unchanged.
- The report reflects the latest 7 real logged trading days available locally, not the latest calendar 7 days, because newer runtime artifacts are test-fixture pollution.
- Roll back by reverting `docs/weekly-perf.md` and the matching `memory/codex-loop/*.md` summary updates.
