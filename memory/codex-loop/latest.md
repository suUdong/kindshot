Hypothesis: If v66 shadow tracking is validated against current paper-runtime data, then guardrail-blocked high-confidence BUYs should produce `shadow_` snapshots, the opportunity-cost report should show skip-reason/hour/stale-data context, and shutdown should no longer drop already-due snapshots during restart.

Changed files:
- `.omx/context/ralph-kindshot-v66-shadow-snapshot-20260327T080258Z.md`
- `.omx/plans/prd-shadow-snapshot-reliability-20260327.md`
- `.omx/plans/test-spec-shadow-snapshot-reliability-20260327.md`
- `src/kindshot/price.py`
- `src/kindshot/main.py`
- `scripts/shadow_analysis.py`
- `tests/test_price.py`
- `tests/test_shadow_analysis.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Live/runtime evidence:
  - local `logs/kindshot_20260327.jsonl` contained 15 guardrail-blocked `BUY` events with `decision_confidence >= 75`; `OPENING_LOW_CONFIDENCE` fired twice at `2026-03-27 09:00 KST`
  - remote `journalctl -u kindshot` after the `2026-03-27 16:42 KST` restart showed `Normalized analysis headline [...]`, `Technical indicator adj [...]`, and `Confidence graduated cap [...]`, confirming v66 news-signal and MACD paths were active in the running paper service
  - remote `/opt/kindshot/data/runtime/price_snapshots/20260327.jsonl` contained 7 rows for `shadow_7c47fb630764e6b2`, proving shadow snapshot collection was live for a guardrail-blocked `BUY`
  - remote shadow event `7c47fb630764e6b2` was `ticker=001510`, `decision_confidence=78`, `skip_reason=MARKET_CLOSE_CUTOFF`, detected at `2026-03-27T16:55:16+09:00`
  - running the updated `scripts/shadow_analysis.py` on copied remote data surfaced the event under `MARKET_CLOSE_CUTOFF`, hour `16:00`, and flagged it as a flat-price / stale suspect (`KIS_REST`)
- Code verification:
  - `python3 -m compileall src/kindshot tests scripts` passed
  - `.venv/bin/python -m pytest tests/test_price.py tests/test_shadow_analysis.py -q` passed (`31 passed, 1 skipped`)
  - `.venv/bin/python -m pytest -q` passed (`815 passed, 1 skipped, 1 warning`)
  - diagnostics on `src/kindshot/price.py`, `scripts/shadow_analysis.py`, and `tests/test_shadow_analysis.py` returned 0 issues

Risk and rollback note:
- Residual risk is operational: paper mode still warns that snapshots use VTS/stale pricing without real KIS API keys, so after-close shadow events can look flat even when collection is functioning.
- Roll back by reverting the shutdown flush / shadow analysis commit; no deploy or secrets changes are involved in this slice.
