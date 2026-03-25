Hypothesis: recent zero-BUY pressure is primarily caused by an overly strict ADV filter, not by a kill switch. Relaxing ADV only for `POS_STRONG` should reopen strong-catalyst flow without weakening `POS_WEAK` risk control.

Changed files:
- `.env.example`
- `deploy/daily_report.py`
- `docs/daily-check-20260325.md`
- `src/kindshot/config.py`
- `src/kindshot/guardrails.py`
- `src/kindshot/hold_profile.py`
- `src/kindshot/pipeline.py`
- `src/kindshot/quant.py`
- `src/kindshot/strategy_observability.py`
- `tests/test_config.py`
- `tests/test_daily_report.py`
- `tests/test_guardrails.py`
- `tests/test_hold_profile.py`
- `tests/test_pipeline.py`
- `tests/test_quant.py`
- `tests/test_strategy_observability.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- Local artifact audit:
  - `logs/kindshot_20260325.jsonl` is missing.
  - `data/runtime/context_cards/20260325.jsonl` is test-generated (`65 rows`, `run_id=test_run`, single event id).
  - `data/runtime/price_snapshots/20260325.jsonl` is test-generated (`115 rows`, `run_id=run1`, single event id).
- Recent real-log analysis:
  - `2026-03-19`: `232` events, `66` `POS_STRONG`, `21` LLM decisions, `2` BUY, `19` SKIP, `45` `ADV_TOO_LOW`, `0` `CONSECUTIVE_STOP_LOSS`.
  - Recent 7 logged days: `50` LLM decisions total, `23` BUY, `27` SKIP.
  - Recent 7 logged days: `ADV_TOO_LOW=240`; with `POS_STRONG_ADV_THRESHOLD=20억`, `42` prior `POS_STRONG` ADV skips would re-enter the candidate set.
  - Recent 7 logged days strategy summary: `TP=2`, `Trailing Stop=2`, `Stop Loss=4`, `Max Hold=4`, `Hold Profile Applied=18`, `Kill Switch Halt=0`, `Market Close Cutoff=7`, `Contract-cancellation NEG=9`.
- Test commands:
  - `source .venv/bin/activate && python -m pytest tests/test_strategy_observability.py tests/test_daily_report.py tests/test_hold_profile.py tests/test_config.py tests/test_quant.py tests/test_guardrails.py tests/test_pipeline.py tests/test_price.py -q` passed (`150 passed`).
  - `source .venv/bin/activate && python -m pytest -q` passed (`527 passed, 1 warning`).

Risk and rollback note:
- Today's operational path is still unverifiable from this workspace until `2026-03-25` runtime logs are synced locally or inspected on the runtime host.
- The new logic intentionally changes only `POS_STRONG`; `POS_WEAK` remains under the stricter general ADV floor.
- Strategy activity is now visible in `deploy/daily_report.py` output and Telegram summaries, and the reconstruction uses pinned strategy defaults instead of live env values so the same log yields the same report later.
- Roll back by reverting the config/quant/guardrail/pipeline changes and removing `POS_STRONG`-specific ADV handling.
