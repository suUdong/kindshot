# Kindshot Codex Loop Process

## Purpose

Use these files together so Codex runs can resume safely across sessions without relying on chat memory alone.

## File Roles

- `memory/codex-loop/roadmap.md`
  - Holds phase ordering, current focus, and next-run candidates.
  - Update only when priorities, phase status, or candidate ordering materially change.
- `memory/codex-loop/latest.md`
  - Holds the most recent run summary.
  - Replace on every run.
- `memory/codex-loop/session.md`
  - Holds the current branch, active hypothesis, environment state, and immediate blocker or next step.
  - Update at the start or end of a run whenever the session state changed.

## Run Order

1. Read `AGENTS.md`.
2. Read `memory/codex-loop/roadmap.md`.
3. Read `memory/codex-loop/session.md`.
4. Read `memory/codex-loop/latest.md`.
5. Choose one bounded hypothesis that advances the current roadmap phase.
6. Implement the smallest reversible slice.
7. Run validation.
8. Update `latest.md`.
9. Update `session.md`.
10. Update `roadmap.md` only if phase or priority changed.

## Validation Notes

- Prefer the project runtime target, Python `3.11`, for full validation.
- If the local interpreter or venv is mismatched, use `uv run --python 3.11 --extra dev pytest -q`.
- Treat ad hoc interpreter mismatches as session-state information and record them in `session.md`.

## Session Rules

- Treat `session.md` as the handoff file between chat sessions.
- Record blockers concretely, including tool or runtime mismatches.
- Record the next intended step as a single actionable item.
- Do not use `session.md` as a changelog.

## Automation Boundary

- Safe to automate:
  - reading and updating run-state files
  - code changes inside the repo
  - compile and test validation
  - branch-based commits and PR preparation
- Keep manual:
  - merge decisions
  - Lightsail deployment
  - production secret changes
  - any live-trading enablement
