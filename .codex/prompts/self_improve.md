You are running the Kindshot self-improvement loop.

Primary goal:
- Improve risk-adjusted return in paper trading mode.

Workflow:
1. Read AGENTS.md and follow it strictly.
2. Read `memory/codex-loop/roadmap.md` and use it as the default source of truth for phase ordering and next-run selection.
3. Read `memory/codex-loop/session.md` and treat it as the handoff file for current branch, blockers, and the immediate next step.
4. Read `memory/codex-loop/latest.md` to avoid repeating the last run.
5. Inspect recent evidence (tests, logs, replay artifacts, guardrail counters).
6. Propose exactly one small, high-confidence hypothesis that advances the current roadmap phase.
7. Implement only the minimum code and test changes required.
8. Run validation:
   - python -m compileall src tests
   - python -m pytest -q
   - if the local interpreter is mismatched, use `uv run --python 3.11 --extra dev pytest -q`
9. Write/update memory/codex-loop/latest.md with:
   - hypothesis
   - changed files
   - validation output summary
   - risk and rollback note
10. Write/update `memory/codex-loop/session.md` with:
   - current branch
   - active hypothesis
   - environment or validation blockers
   - next intended step
11. If priorities or phase state changed, update `memory/codex-loop/roadmap.md` in the same run.

Guardrails:
- Never modify deploy/* files.
- Never modify secrets or .env files.
- Never enable or force live trading behavior.
- If confidence is low or evidence is weak, do not change code; only report findings.
