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

## KIS API Reference

- 공식 예제 레포: https://github.com/koreainvestment/open-trading-api
- LLM용 예제: `examples_llm/domestic_stock/` 하위 API별 폴더
- KIS API 파라미터 동작이 불확실할 때 위 레포의 예제를 반드시 참조할 것
- 주요 주의사항:
  - `FID_INPUT_HOUR_1`: 빈 문자열 = 현재 기준 최신, 값 입력 시 해당 시간 **이전** 데이터 반환
  - `FID_INPUT_DATE_1`: 빈 문자열 = 현재 기준, 포맷 `00YYYYMMDD`
  - 페이지네이션: 응답 헤더 `tr_cont == "M"`이면 다음 페이지 존재
