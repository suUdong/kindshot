Hypothesis: If KIS/KIND cross-source duplicates are collapsed, KIS noise filtering is stricter, and low-information BUY headlines receive a deterministic post-LLM penalty, the paper-trading pipeline should spend less attention on article noise and duplicate disclosures without weakening confirmed corporate-action events.

Changed files:
- `src/kindshot/event_registry.py`
- `src/kindshot/feed.py`
- `src/kindshot/guardrails.py`
- `src/kindshot/pipeline.py`
- `tests/test_event_registry.py`
- `tests/test_feed.py`
- `tests/test_guardrails.py`
- `docs/plans/2026-03-27-disclosure-quality-hardening.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- `EventRegistry` now skips same-content KIS/KIND duplicates while preserving same-source same-title events.
- `KisFeed` now filters extra institutional-flow/chart/theme noise and admits several additional disclosure-style corporate-action keywords.
- BUY decisions now receive an extra headline-quality penalty for very short, speculative, or amount-free contract headlines before downstream confidence flooring.
- Added regression coverage for cross-source dedup, same-source preservation, day rollover cleanup, expanded KIS noise filtering, added disclosure keyword pass-through, and headline-quality penalties.
- `python3 -m compileall src/kindshot` passed.
- `.venv/bin/python -m pytest tests/test_event_registry.py tests/test_feed.py tests/test_guardrails.py -q` passed (`188 passed`)

Risk and rollback note:
- This slice changes disclosure ingest/evaluation behavior only; it does not touch live execution enablement, deployment paths, or secrets handling.
- The expanded disclosure keyword list is intentionally conservative but could still admit some borderline industry-theme headlines; future runtime logs should confirm whether the extra recall is acceptable.
- Roll back by reverting the four source files above, their tests, the design note, and the session summary updates.
