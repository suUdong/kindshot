Hypothesis: If Kindshot uses a supportive-market dynamic guardrail profile and exposes blocked-vs-passed review in the existing backtest analysis surface, operators can reduce confidence-driven overblocking without weakening chase-buy, liquidity, or market-close hard stops.

Changed files:
- `.omx/context/ralph-guardrail-recalibration-20260327T083004Z.md`
- `.omx/plans/prd-guardrail-recalibration-20260327.md`
- `.omx/plans/test-spec-guardrail-recalibration-20260327.md`
- `docs/plans/2026-03-27-guardrail-recalibration.md`
- `scripts/backtest_analysis.py`
- `src/kindshot/config.py`
- `src/kindshot/guardrails.py`
- `src/kindshot/pipeline.py`
- `tests/test_backtest_analysis.py`
- `tests/test_guardrails.py`
- `tests/test_pipeline.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- `python3 -m compileall src/kindshot scripts tests` passed
- `.venv/bin/python -m pytest tests/test_guardrails.py tests/test_pipeline.py tests/test_backtest_analysis.py -q` passed (`182 passed`)
- `.venv/bin/python -m pytest -q` passed (`834 passed, 1 skipped, 1 warning`)
- diagnostics on `src/kindshot/guardrails.py`, `src/kindshot/pipeline.py`, `scripts/backtest_analysis.py`, `tests/test_guardrails.py`, `tests/test_pipeline.py`, and `tests/test_backtest_analysis.py` returned 0 issues
- `.venv/bin/python scripts/backtest_analysis.py --dates 20260326 20260327 --format both --output logs/daily_analysis/guardrail_recalibration_20260326_20260327.txt` passed
- remote `python3 -m compileall src/kindshot scripts tests` passed on `kindshot-server`
- remote `pip install -e . --quiet` passed on `kindshot-server`
- remote `systemctl restart kindshot` succeeded and service returned `active (running)` at `2026-03-27 17:42:56 KST`
- remote `python3 /opt/kindshot/scripts/trading_log_report.py --date 20260327` passed
- remote `python3 scripts/shadow_analysis.py --dates 20260327` passed
- remote `python3 scripts/backtest_analysis.py --dates 20260326 20260327 --format json --output logs/daily_analysis/guardrail_recalibration_server_20260326_20260327.txt` passed

Analysis result:
- Server-side guardrail review across `2026-03-26` and `2026-03-27` now reports `41` inline BUY intents with `36` blocked (`87.8%`) and `5` passed/replayed BUYs.
- The dominant blocker remains `LOW_CONFIDENCE` (`23`, `63.9%` share), followed by `FAST_PROFILE_LATE_ENTRY` (`5`).
- Available shadow coverage is still sparse: only `2` blocked BUYs have shadow traces on the server (`5.6%` coverage), and both were flat `0.00%` on `KIS_REST`, so opportunity-cost evidence is still limited.
- Runtime now computes a supportive-market dynamic profile that relaxes:
  - base `min_buy_confidence` by `2` points (floor `76`)
  - `opening_min_confidence` by `1` point (floor `80`)
  - `afternoon_min_confidence` by `2` points (floor `78`)
  - `fast_profile_no_buy_after` by up to `60` minutes, capped by the global market-close cutoff
- Hard stops for `CHASE_BUY_BLOCKED`, `ORDERBOOK_TOP_LEVEL_LIQUIDITY`, and `MARKET_CLOSE_CUTOFF` remain unchanged.

Risk and rollback note:
- Residual risk is evidence sparsity: blocked-BUY outcome coverage is still too thin to justify broader relaxation than the small supportive-market adjustments shipped here.
- Server-side targeted pytest could not run because `/opt/kindshot/.venv` does not have `pytest`; remote verification used `compileall`, install, restart, log report, and analysis script smoke checks instead.
- Roll back by reverting commit `652e414`, re-syncing `scripts/backtest_analysis.py`, `src/kindshot/config.py`, `src/kindshot/guardrails.py`, `src/kindshot/pipeline.py`, and the matching test/docs files, then restarting `kindshot`.

---

Hypothesis: If Kindshot v66 trade-history analysis is expanded into a full-history matrix + recommendation surface, operators can derive entry/exit tuning candidates from local evidence without mutating runtime strategy defaults during the same run.

Changed files:
- `.omx/context/ralph-v66-trading-analysis-20260327T081112Z.md`
- `.omx/plans/prd-v66-trading-analysis-auto-tune-20260327.md`
- `.omx/plans/test-spec-v66-trading-analysis-auto-tune-20260327.md`
- `docs/plans/2026-03-27-v66-trading-analysis-auto-tune.md`
- `scripts/backtest_analysis.py`
- `scripts/auto_tune_strategy.py`
- `tests/test_backtest_analysis.py`
- `tests/test_auto_tune_strategy.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- `python3 -m compileall src/kindshot tests scripts` passed
- `.venv/bin/python -m pytest tests/test_backtest_analysis.py tests/test_auto_tune_strategy.py -q` passed (`7 passed`)
- `.venv/bin/python -m pytest -q` passed (`827 passed, 1 skipped, 1 warning`)
- diagnostics on `scripts/backtest_analysis.py`, `scripts/auto_tune_strategy.py`, `tests/test_backtest_analysis.py`, and `tests/test_auto_tune_strategy.py` returned 0 issues
- `.venv/bin/python scripts/backtest_analysis.py --format both --output logs/daily_analysis/backtest_v66_deep_report.txt` passed
- `.venv/bin/python scripts/auto_tune_strategy.py --analysis logs/daily_analysis/backtest_v66_deep_report.json --format json --output logs/daily_analysis/auto_tune_v66.json` passed

Analysis result:
- Local full-history reconstruction still yields `14` executed BUY trades from the available `kindshot_*.jsonl` set.
- The strongest positive cohort in the current sample is `11:00` KST entries (`3` trades, `66.7%` win rate, `+0.095%` avg PnL).
- The exit-optimization sweep did not beat the current v66 baseline on this sparse sample, so the generated auto-tune recommendation keeps the existing exit parameters and fast-profile cutoff unchanged.

Risk and rollback note:
- Residual risk is sample-size driven: the local reconstructable history is still only `14` trades, so the new tuner is currently most useful as a consistency/checking surface rather than a strong optimization oracle.
- Roll back by reverting the analysis/tuning script additions and deleting the generated `logs/daily_analysis/backtest_v66_deep_report.{txt,json}` and `logs/daily_analysis/auto_tune_v66.json` artifacts.

---

Hypothesis: If Kindshot can ingest macro regime over HTTP from `macro-intelligence`, Korea-equity decisions can use the shared macro layer without coupling to the macro DB or Python package layout.

Changed files:
- `docs/design/2026-03-27-macro-http-regime.md`
- `src/kindshot/config.py`
- `src/kindshot/models.py`
- `src/kindshot/market.py`
- `src/kindshot/decision.py`
- `tests/test_market.py`
- `tests/test_decision.py`

Validation:
- `python3 -m compileall src/kindshot tests` passed
- `.venv/bin/python -m pytest tests/test_market.py tests/test_decision.py -q` passed (`60 passed`)
- `.venv/bin/python -m pytest tests/test_context_card.py -q` passed (`17 passed`)
- diagnostics on `src/kindshot/market.py` returned 0 issues

Risk and rollback note:
- Residual risk is operational rather than code-level: macro HTTP fetch currently fails open and logs warnings, so stale macro context is possible if the upstream service is down.
- Roll back by removing `MACRO_API_BASE_URL` usage and reverting the market-context / prompt field additions.

---

Additional integration note (2026-03-27):

- `kindshot` now supports optional macro regime HTTP reads from `macro-intelligence` via `MACRO_API_BASE_URL`.
- Updated files for this slice:
  - `docs/design/2026-03-27-macro-http-regime.md`
  - `src/kindshot/config.py`
  - `src/kindshot/models.py`
  - `src/kindshot/market.py`
  - `src/kindshot/decision.py`
  - `tests/test_market.py`
  - `tests/test_decision.py`
- Validation:
  - `python3 -m compileall src/kindshot tests` passed
  - `.venv/bin/python -m pytest tests/test_market.py tests/test_decision.py tests/test_context_card.py -q` passed (`77 passed`)
- Rollback note:
  - Clear `MACRO_API_BASE_URL` or revert the listed files to remove macro HTTP context injection.
