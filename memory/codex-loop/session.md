# Kindshot Codex Session State

## Current Session

- Branch: `main`
- Phase: `User-Directed LLM Prompt Optimization`
- Focus: measure the current post-v70 LLM decision-path quality, tighten prompt confidence semantics, and cut avoidable late-entry LLM calls without touching deploy/live-order surfaces.
- Active hypothesis: if short-hold fast-profile late entries are blocked before the LLM and the prompt is stricter about confidence over the actual hold profile, then the runtime path will waste fewer calls while keeping prompt behavior reviewable through the new offline evaluator.
- Blocker: live Anthropic prompt replay is blocked on this host by `invalid_request_error: credit balance is too low`; historical baseline measurement and runtime rollout are complete.

## Environment

- Host: local workspace
- Runtime target: AWS Lightsail `kindshot-server` (`/opt/kindshot`, paper mode)
- Local note: runtime fix was committed as `425c07d`, pushed to `origin/main`, then `src/kindshot/decision.py`, `src/kindshot/pipeline.py`, `src/kindshot/prompts/decision_strategy.txt`, and `scripts/llm_prompt_eval.py` were rsynced to `/opt/kindshot/` before restarting `kindshot`
- Validation status:
  - local `python3 -m compileall src scripts tests dashboard` passed
  - local targeted pytest (`tests/test_llm_prompt_eval.py tests/test_pipeline.py tests/test_decision.py`) passed (`87 passed`)
  - local full `pytest -q` passed (`977 passed, 1 skipped, 1 warning`)
  - local diagnostics on changed files returned `0 errors`
  - local prompt-eval artifact recorded `accuracy=0.625` on a balanced `16`-case historical sample, with `8 / 10` fast-profile cases happening after the late-entry cutoff
  - remote `python3 -m compileall src/kindshot scripts` passed
  - remote `systemctl is-active kindshot` returned `active`
  - remote `/health` returned `healthy` with `last_poll_source=feed`, `last_poll_age_seconds=11`, and `guardrail_state.configured_max_positions=4`
  - remote journal after restart showed `Health server started` and `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=14 boost=1 loss=2`

## Last Completed Step

- Added the offline prompt-eval script, updated prompt confidence guidance, moved `FAST_PROFILE_LATE_ENTRY` ahead of the LLM call, pushed `425c07d`, deployed the runtime patch, and re-verified remote `kindshot` health.

## Next Intended Step

- Restore provider credits (or switch to an available prompt-replay provider) and rerun `scripts/llm_prompt_eval.py --prompt ...` to get an actual baseline-vs-variant replay comparison instead of the current blocked status.
- Observe the next Korean market session to confirm `FAST_PROFILE_LATE_ENTRY` now suppresses avoidable LLM calls before runtime decision records would otherwise be generated.
- If replay evidence supports it, choose one narrower prompt variant for the current false-negative cluster rather than broadening BUY behavior globally.

## Notes

- `2026-03-28` is a Saturday in KST, so same-day prompt-path execution could not be observed on fresh market events.
- This run changed prompt/runtime decision-path code only; it did not alter `deploy/`, secrets, `.env`, or live-order enablement.
- Fresh evidence is recorded in `DEPLOYMENT_LOG.md` and `memory/codex-loop/latest.md`.
