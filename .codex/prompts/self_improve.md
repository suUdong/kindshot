You are running the Kindshot self-improvement loop.

Primary goal:
- Improve risk-adjusted return in paper trading mode.

Workflow:
1. Read AGENTS.md and follow it strictly.
2. Inspect recent evidence (tests, logs, replay artifacts, guardrail counters).
3. Propose exactly one small, high-confidence hypothesis.
4. Implement only the minimum code and test changes required.
5. Run validation:
   - python -m compileall src tests
   - python -m pytest -q
6. Write/update memory/codex-loop/latest.md with:
   - hypothesis
   - changed files
   - validation output summary
   - risk and rollback note

Guardrails:
- Never modify deploy/* files.
- Never modify secrets or .env files.
- Never enable or force live trading behavior.
- If confidence is low or evidence is weak, do not change code; only report findings.
