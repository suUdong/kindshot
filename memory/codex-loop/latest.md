Hypothesis: If Kindshot measures the live LLM decision path against recorded price follow-through, tightens prompt confidence semantics for short-hold contract/news cases, and moves `FAST_PROFILE_LATE_ENTRY` blocking ahead of the LLM call, then it can improve prompt-path discipline and reduce wasted LLM calls without changing deploy scripts or live-order behavior.

Changed files:
- `src/kindshot/decision.py`
- `src/kindshot/pipeline.py`
- `src/kindshot/prompts/decision_strategy.txt`
- `scripts/llm_prompt_eval.py`
- `tests/test_pipeline.py`
- `tests/test_llm_prompt_eval.py`
- `docs/design/2026-03-28-llm-prompt-optimization.md`
- `DEPLOYMENT_LOG.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`
- `memory/codex-loop/roadmap.md`

Implementation summary:
- Added `scripts/llm_prompt_eval.py` to reconstruct historical LLM decisions from local logs/context cards, derive hindsight BUY/SKIP labels from recorded price paths, and report baseline accuracy/calibration plus fast-profile late-entry call-avoidance candidates.
- Updated `decision.py` so offline tooling can replay prompt variants via `strategy_override` without changing the normal runtime path.
- Moved `FAST_PROFILE_LATE_ENTRY` blocking ahead of the LLM call in `pipeline.py`, preserving the same guardrail outcome while preventing unnecessary provider calls on non-executable 20-minute late entries.
- Tightened `decision_strategy.txt` so confidence explicitly means the correctness probability of the chosen action over the actual hold profile, with extra caution around short-hold contract headlines near the open and late session.
- Pushed commit `425c07d`, rsynced the changed runtime files plus the new eval script to `kindshot-server:/opt/kindshot/`, restarted `kindshot`, and re-verified remote health.

Validation:
- local `python3 -m compileall src scripts tests dashboard`
- local `.venv/bin/python -m pytest tests/test_llm_prompt_eval.py tests/test_pipeline.py tests/test_decision.py -q` → `87 passed`
- local `.venv/bin/python -m pytest -q` → `977 passed, 1 skipped, 1 warning`
- local changed-file diagnostics on `src/kindshot/decision.py`, `src/kindshot/pipeline.py`, `scripts/llm_prompt_eval.py`, `tests/test_pipeline.py`, `tests/test_llm_prompt_eval.py` → `0 errors`
- local prompt-eval artifact `logs/daily_analysis/llm_prompt_eval_20260328.{txt,json}` recorded:
  - `16` balanced cases (`8 BUY target`, `8 SKIP target`)
  - historical actual: `accuracy=0.625`, `buy_precision=1.0`, `skip_precision=0.5714`, `buy_recall=0.25`, `false_negative_rate=0.75`
  - fast-profile late cost candidates: `8 / 10`
  - live prompt replay attempt: blocked by Anthropic credit error
- remote `python3 -m compileall src/kindshot scripts`
- remote `systemctl is-active kindshot` → `active`
- remote `/health` returned:
  - `status: "healthy"`
  - `last_poll_source: "feed"`
  - `last_poll_age_seconds: 11`
  - `guardrail_state.configured_max_positions: 4`
  - `recent_pattern_profile.total_trades: 14`
- remote journal after restart showed:
  - `kindshot 0.1.3 starting`
  - `Health server started on 127.0.0.1:8080`
  - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=14 boost=1 loss=2`

Simplifications made:
- Reused the existing `rsync` + systemd restart deployment lane instead of introducing new deployment tooling.
- Reduced LLM cost by moving an already-known late-entry guardrail earlier, rather than inventing a new broad skip rule.
- Kept prompt A/B tooling separate from runtime behavior so future prompt experiments stay reviewable.

Remaining risks:
- `2026-03-28` is a Saturday in KST, so fresh same-day news-to-trade execution could not be observed; end-to-end confirmation still relies on historical logs plus runtime health.
- Live prompt replay against the current Anthropic model could not complete because the account on this host returned `invalid_request_error: credit balance is too low`.
- The current historical baseline still shows high SKIP false-negative rate (`0.75`) on the balanced eval sample, so the next useful slice is to rerun prompt variants once provider credits are restored and compare against this artifact.
- The server still runs in VTS pricing mode, so stale-price warnings remain expected until real quote keys are configured.
- `tests/test_health.py` still emits the pre-existing aiohttp `NotAppKeyWarning`; this run did not change that path.

Rollback note:
- Re-sync the prior runtime files from `6d1a3f4` (or revert `425c07d`), then restart `kindshot`.
