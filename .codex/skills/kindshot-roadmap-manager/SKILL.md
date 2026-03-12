---
name: kindshot-roadmap-manager
description: Maintain roadmap-driven execution for Kindshot Codex improvement runs. Use when Codex needs to choose the next bounded task, align a self-improve run to the current phase, or update roadmap and run-summary state after completing work.
---

# Kindshot Roadmap Manager

## Workflow

1. Read `AGENTS.md`, `memory/codex-loop/roadmap.md`, `memory/codex-loop/session.md`, and `memory/codex-loop/latest.md` before proposing work.
2. Treat `memory/codex-loop/roadmap.md` as the ordering source of truth unless the user explicitly overrides it.
3. Treat `memory/codex-loop/session.md` as the current handoff state for branch, blocker, and next-step context.
4. Choose one hypothesis that fits the active phase and keeps the diff small and reversible.
5. Prefer work that improves data correctness, guardrails, observability, or automation discipline before adding new trading behavior.
6. After changes and validation, update `memory/codex-loop/latest.md`.
7. Update `memory/codex-loop/session.md` whenever branch, blocker, active hypothesis, or next step changed.
8. Update `memory/codex-loop/roadmap.md` only when phase status, current focus, or next-run candidates materially changed.

## Selection Rules

- Pick the first high-confidence candidate from the active phase unless recent evidence points to a clearer defect.
- If a task would span multiple hypotheses, split it and complete only the first reversible slice.
- If KIS API semantics are unclear, verify against the official example repository before editing code.
- Do not choose tasks that require `deploy/` edits, secret changes, or live-trading activation.

## Output Rules

- Keep run summaries short and reviewable.
- Record hypothesis, changed files, validation result, and rollback note in `memory/codex-loop/latest.md`.
- Record current branch, blocker, and next intended step in `memory/codex-loop/session.md`.
- Keep roadmap entries phase-based, not changelog-shaped.
- Prefer Python `3.11` validation; if the local environment is mismatched, use `uv run --python 3.11 --extra dev pytest -q`.

## When Blocked

- If evidence is weak, update the run summary with findings and avoid code changes.
- If the next roadmap task depends on missing environment access or unstable external behavior, leave the phase unchanged and record the blocker in both `latest.md` and `session.md`.
