Hypothesis: The next evidence gap after the NVIDIA day1 report is confidence-shape drift tracking. A standalone confidence-distribution report that highlights exact-value mode share, confidence bands, source-split cohorts, collapse flags, and an explicit `LLM` before/after verdict should make it obvious whether the LLM upgrade actually broke the old `confidence=50` collapse or merely changed the surface text.

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
  - an explicit `LLM` before/after delta row with `improved` / `unchanged` / `regressed` / `insufficient-data`
- Hardened the delta renderer so partial test fixtures without per-source medians do not raise `KeyError`
- Added parser/render coverage in `tests/test_confidence_report.py`
- `git diff --check` passed
- `.venv/bin/python -m py_compile scripts/confidence_report.py` passed
- `.venv/bin/python -m pytest tests/test_confidence_report.py -q` passed (`5 passed`)
- `.venv/bin/python -m pytest -q` passed outside sandbox (`706 passed, 1 warning`)

Risk and rollback note:
- This slice changes only analysis tooling and documentation; it does not change strategy, execution, or deployment wiring.
- The report intentionally keys off `decision_source` and logged confidence values; it does not try to infer the true upstream provider/model from `llm_model`.
- Roll back by reverting `scripts/confidence_report.py`, the new tests/doc change, and restoring the previous `memory/codex-loop/latest.md` / `memory/codex-loop/session.md`.
