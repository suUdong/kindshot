Hypothesis: If Kindshot consumes alpha-scanner's FP-filtered high-conviction feed as a dedicated `ALPHA` strategy signal, then Alpha BUY candidates can enter paper execution through the existing strategy-runtime guardrails without weakening current news risk controls.

Changed files:
- `src/kindshot/main.py`
- `memory/codex-loop/latest.md`

Relevant pre-existing AlphaFeed support verified in this run:
- `src/kindshot/feed.py`
- `src/kindshot/config.py`
- `tests/test_alpha_feed.py`
- `tests/test_config.py`

Implementation summary:
- Registered `AlphaFeed` in `_build_strategy_registry()` when `ALPHA_FEED_ENABLED=true` and `ALPHA_SCANNER_API_BASE_URL` is configured.
- AlphaFeed emits `TradeSignal(source=ALPHA, action=BUY)`, so candidates use the existing `consume_strategy_signals()` execution path.
- Preserved live safety: AlphaFeed is disabled by default, requires an explicit alpha-scanner base URL, and existing paper/live guardrails remain the execution gate.
- Removed dead imports/unused tracer assignment while keeping `init_tracer()` side effects.

Validation:
- `pytest tests/test_alpha_feed.py tests/test_config.py tests/test_technical_strategy.py -q` -> `34 passed`
- `python3 -m compileall src tests` -> success
- `python3 -m ruff check src/kindshot/config.py src/kindshot/feed.py src/kindshot/main.py tests/test_alpha_feed.py tests/test_config.py` -> success

Dry-run / signal-flow evidence:
- `tests/test_alpha_feed.py` verifies alpha-scanner payload -> `TradeSignal(source=ALPHA, action=BUY)` with KRX filtering, confidence thresholding, size mapping, metadata, and dedup.
- `tests/test_technical_strategy.py` verifies strategy BUY signals execute through event/decision records, scheduler, and `GuardrailState.record_buy()` in paper mode.

Rollback note:
- Set `ALPHA_FEED_ENABLED=false` or revert `src/kindshot/main.py`. No deploy files, secrets, or live-order defaults were changed.
