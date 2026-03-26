Hypothesis: In the current environment, the replay bottleneck is operational rather than strategic: fresh LLM reruns are blocked by missing NVIDIA credentials and exhausted Anthropic credits, while `rule_fallback` remains executable and materially reduces loss by trading far less.

Changed files:
- `docs/replay-2weeks.md`
- `memory/codex-loop/latest.md`

Validation:
- Re-ran `replay.py` over the reliable recent-window logs: `20260313`, `20260316`, `20260317`, `20260318`, `20260319`
- Verified `20260322` through `20260326` runtime artifacts are polluted (`run_id=test_run`, repeated single `event_id`) and excluded them from the comparison window
- Built local comparison artifacts under `.omc/replay_2weeks/` for:
  - current replay rerun status
  - full-window `rule_fallback` replay
  - historical logged LLM vs current `rule_fallback` on the same `27` decided events

Risk and rollback note:
- This slice is documentation-only and does not change runtime behavior.
- The requested fresh `NVIDIA LLM vs rule_fallback` replay could not complete because `NVIDIA_API_KEY` is unset and the Anthropic fallback account returned low-credit `400` errors.
- The historical logged LLM benchmark is only comparable on the `27` events that actually have local decision records, so it should not be read as a full-window rerun substitute.
- Roll back by reverting `docs/replay-2weeks.md` and restoring the previous `memory/codex-loop/latest.md`.
