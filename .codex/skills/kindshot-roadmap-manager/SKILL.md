---
name: kindshot-roadmap-manager
description: Maintain roadmap-driven execution for Kindshot Codex improvement runs. Use when Codex needs to choose the next bounded task, align a self-improve run to the current phase, or update roadmap and run-summary state after completing work.
---

# Kindshot Roadmap Manager

## Workflow

1. Read `AGENTS.md`, `memory/codex-loop/roadmap.md`, `memory/codex-loop/session.md`, and `memory/codex-loop/latest.md` before proposing work.
2. If `memory/codex-loop/ops-backlog.md` exists, read it too and treat it as the source of truth for log-driven cleanup slices.
3. Treat `memory/codex-loop/roadmap.md` as the ordering source of truth for strategy/phase work unless the user explicitly overrides it.
4. Treat `memory/codex-loop/session.md` as the current handoff state for branch, blocker, and next-step context.
5. Choose one hypothesis that fits the active phase and keeps the diff small and reversible, unless the user explicitly requested backlog batch mode.
6. Prefer work that improves data correctness, guardrails, observability, or automation discipline before adding new trading behavior.
7. After changes and validation, update `memory/codex-loop/latest.md`.
8. Update `memory/codex-loop/session.md` whenever branch, blocker, active hypothesis, or next step changed.
9. Update `memory/codex-loop/roadmap.md` only when phase status, current focus, or next-run candidates materially changed.
10. Update `memory/codex-loop/ops-backlog.md` when an operational cleanup item changes status, ordering, evidence, or mode assumptions.

## Selection Rules

- If `ops-backlog.md` contains active items, prefer the first active `P0`, then `P1`, then `P2` item before roadmap candidate work.
- If the user explicitly requests batch mode, execute consecutive active items from `ops-backlog.md` using that file's batch rules.
- Pick the first high-confidence candidate from the active phase unless recent evidence points to a clearer defect.
- If a task would span multiple hypotheses, split it and complete only the first reversible slice.
- If KIS API semantics are unclear, verify against the official example repository before editing code.
- Do not choose tasks that require `deploy/` edits, secret changes, or live-trading activation.

## Output Rules

- Keep run summaries short and reviewable.
- Record hypothesis, changed files, validation result, and rollback note in `memory/codex-loop/latest.md`.
- Record current branch, blocker, and next intended step in `memory/codex-loop/session.md`.
- Keep roadmap entries phase-based, not changelog-shaped.
- Keep `ops-backlog.md` ordered and operational; it is a queue, not a postmortem.
- When batch mode is used, summarize the completed batch items together and record the next untouched active item.
- Prefer Python `3.11` validation; if the local environment is mismatched, use `uv run --python 3.11 --extra dev pytest -q`.
- When describing progress or proposing the next slice to the user, include a short progress snapshot in flat-list form so status is scannable at a glance.
- Use this exact shape when relevant:
  - `Done:` most recent completed slice from `session.md`
  - `Current:` active hypothesis for this run
  - `Next:` next intended step or the next 1-3 bounded slices from roadmap/review evidence
- Keep the snapshot brief and operational; do not turn it into a changelog.

## When Blocked

- If evidence is weak, update the run summary with findings and avoid code changes.
- If the next roadmap task depends on missing environment access or unstable external behavior, leave the phase unchanged and record the blocker in both `latest.md` and `session.md`.
