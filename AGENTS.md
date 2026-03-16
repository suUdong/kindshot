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
- Default to batch-mode execution for implementation work: once a bounded slice is chosen, continue through design, implementation, tests, and run-summary updates without pausing for confirmation unless a real blocker appears.
- For any non-trivial feature, strategy, or pipeline behavior change, complete a detailed analysis/design pass and write/update the relevant design doc before implementation.
- Do not start implementation for a new feature hypothesis until the design doc captures scope, rollout, logging, validation, and rollback expectations.
- Add or update tests for behavior changes.
- Avoid schema-breaking logging changes unless explicitly requested.

## Batch Mode Rules

- Treat feature development the same as backlog cleanup for execution cadence: finish a meaningful bounded slice before reporting back.
- Set the default slice size at the capability level, not the helper level: one batch should usually finish a user-visible or operator-visible workflow, CLI path, or report path end-to-end.
- Do not report progress after adding internal plumbing only; keep going until the batch closes the surrounding usable capability unless a real blocker appears.
- Prefer batches that bundle the main behavior change with the dependent reader/writer wiring, output shape, tests, and docs needed to make that behavior actually usable.
- Avoid ending a batch at intermediate states like "index added", "helper added", or "sink added" if the corresponding feature still cannot be exercised as a coherent path.
- Do not stop for intermediate approval once implementation has started unless a real blocker appears.
- Real blockers are limited to missing credentials or network access, missing permissions, unresolved user-choice forks with material risk, or direct conflicts with existing user changes.
- If blocked, stop at the blocker, record it clearly, and preserve the next executable step in the session summary.
- If not blocked, continue to the next bounded slice that matches the active design and phase guidance.

## Validation

- Run compile and tests after edits.
- If tests cannot run in the current environment, report the gap explicitly.
- Write a run summary to `memory/codex-loop/latest.md`.

## Design-First Workflow

- Treat design as a hard gate, not a nice-to-have, for meaningful behavior changes.
- When a user requests a new feature or strategy change, first:
  - analyze the current behavior and constraints
  - write or update a detailed design/plan document
  - define rollout stages, observability/logging, and validation
- Only after that design is written and reviewed in the workspace should implementation begin.
- If the user explicitly wants design first, keep the run documentation-only unless they later ask to build it.

## Session Resume Workflow

- If the user says they want to continue from the previous session, first read `memory/codex-loop/session.md`, `memory/codex-loop/latest.md`, and `memory/codex-loop/roadmap.md` before proposing or making changes.
- Treat those files as the handoff source of truth for current focus, active hypothesis, completed step, next intended step, and validation state.
- After restoring that context, continue from the recorded next step unless the user explicitly redirects the priority.

## Output Requirements

- Include hypothesis, changed files, validation result, and rollback note.
- Keep production/deployment behavior unchanged unless explicitly requested.

## KIS API Reference

- 공식 예제 레포: https://github.com/koreainvestment/open-trading-api
- LLM용 예제: `examples_llm/domestic_stock/` 하위 API별 폴더
- KIS API 파라미터 동작이 불확실할 때 위 레포의 예제를 반드시 참조할 것
- 주요 주의사항:
  - `FID_INPUT_HOUR_1`: 빈 문자열 = 현재 기준 최신, 값 입력 시 해당 시간 **이전** 데이터 반환
  - `FID_INPUT_DATE_1`: 빈 문자열 = 현재 기준, 포맷 `00YYYYMMDD`
  - 페이지네이션: 응답 헤더 `tr_cont == "M"`이면 다음 페이지 존재
