# Kindshot Agent Rules

## Objective

- Improve risk-adjusted returns for Kindshot paper trading.
- Prioritize robustness and drawdown control over raw return.

## Hard Safety Rules

- Never enable live order execution in automation.
- Never edit files under `deploy/` in automated improvement runs.
- Never modify secrets, `.env`, or credential handling.
- Never bypass validation after code changes.

## Change Scope

- Apply exactly one strategy hypothesis per run.
- Keep diffs small and reversible.
- Add or update tests for behavior changes.
- Avoid schema-breaking logging changes unless explicitly requested.

## Validation

- Run compile and tests after edits.
- If tests cannot run in the current environment, report the gap explicitly.
- Write a run summary to `memory/codex-loop/latest.md`.

## Output Requirements

- Include hypothesis, changed files, validation result, and rollback note.
- Keep production/deployment behavior unchanged unless explicitly requested.
