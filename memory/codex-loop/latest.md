Hypothesis: The next evidence gap after the NVIDIA day1 report is confidence-shape drift tracking. A standalone confidence-distribution report that highlights exact-value mode share, confidence bands, source-split cohorts, and collapse flags should make it obvious whether the LLM upgrade actually broke the old `confidence=50` collapse or merely changed the surface text.

Changed files:
- `scripts/confidence_report.py`
- `tests/test_confidence_report.py`
- `docs/plans/2026-03-27-confidence-distribution-report.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Added `scripts/confidence_report.py` to summarize:
  - exact confidence frequencies
  - confidence bands
  - action split by band
  - source-split confidence cohorts
  - mode confidence / mode share / collapse flag
  - multi-cohort comparison rows
- Added parser/render coverage in `tests/test_confidence_report.py`
- `git diff --check` passed
- `python3 -m py_compile scripts/confidence_report.py` passed
- `source .venv/bin/activate && python -m pytest tests/test_confidence_report.py -q` passed (`4 passed`)
- `python3 scripts/confidence_report.py --log-file /tmp/kindshot-nvidia-day1/kindshot_20260326.jsonl` reported:
  - `source[LLM]: n=30 mode=50 mode_share=100.0% flag=collapsed`
  - `source[RULE_FALLBACK]: n=14 mode=72 mode_share=71.4% flag=clustered`
  - overall median `50`, top exact values `50:30, 72:10, 45:4`

Risk and rollback note:
- This slice changes only analysis tooling and documentation; it does not change strategy, execution, or deployment wiring.
- The report intentionally keys off `decision_source` and logged confidence values; it does not try to infer the true upstream provider/model from `llm_model`.
- Roll back by reverting `scripts/confidence_report.py`, the new tests/doc change, and restoring the previous `memory/codex-loop/latest.md` / `memory/codex-loop/session.md`.
