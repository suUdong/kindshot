Hypothesis: If `910a331`의 v78 가드레일 완화 산출물을 raw throughput 면과 deduped profitability 면으로 분리해 재검증하면, 완화 효과의 실제 크기와 과장 위험을 더 명확하게 판단할 수 있다.

Changed files:
- `docs/plans/2026-03-29-v78-guardrail-profitability-validation.md`
- `scripts/backtest_signals.py`
- `scripts/v78_guardrail_profitability_validation.py`
- `tests/test_backtest_signals.py`
- `tests/test_v78_guardrail_profitability_validation.py`
- `reports/v78-guardrail-profitability-validation.md`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Implementation summary:
- Added a dedicated validation script that parses `reports/signal-backtest-result.md`, reloads `pykrx` close data, verifies the reported T+1/T+5 returns, and cross-references `reports/guardrail_sim.json` for pre/post block-rate comparison.
- Generated a new report under `reports/` that separates the two evidence surfaces: raw guardrail throughput (`229` eligible events from `guardrail_sim.json`) and deduped profitability (`42` rows from `signal-backtest-result.md`).
- Documented the main `910a331` caveat: the original summary mixes `87 total BUY / 42 deduped pass / 32 raw blocked`, which is not a single denominator. The validation report infers `55` raw passes and `13` duplicate-pass removals instead of treating `42+32` as a valid total.
- Updated `scripts/backtest_signals.py` so future reruns can take `--db-path` / `--report-path` and report `raw pass`, `deduped pass`, and `duplicate removed` counts explicitly instead of hiding that framing gap.

Validation:
- `python3 scripts/v78_guardrail_profitability_validation.py`
- `python3 -m pytest tests/test_v78_guardrail_profitability_validation.py tests/test_backtest_signals.py -q` → `5 passed`
- `python3 -m compileall scripts/v78_guardrail_profitability_validation.py scripts/backtest_signals.py tests/test_v78_guardrail_profitability_validation.py tests/test_backtest_signals.py`
- `lsp_diagnostics_directory` on workspace → `0 errors`, `0 warnings`
- architect verification via `codex exec` → `APPROVED`

Key findings:
- `signal-backtest-result.md` 상세 42행은 현재 `pykrx` 종가로 재검산했을 때 T+1/T+5 mismatch `0건`이었다.
- Throughput 면에서 v78 완화 효과는 `132 -> 136 pass`, `97 -> 93 block`, `57.6% -> 59.4% pass rate`, `42.4% -> 40.6% block rate`로 확인됐다.
- Profitability 면에서 전체 T+1 평균은 `-4.25%`, 전체 T+5 평균은 `+2.39%`로 재확인됐다.
- Deduped 표본 안에서 기존 `PASSED` cohort의 T+5 평균은 `-0.81%`, 완화로 새로 편입된 cohort의 T+5 평균은 `+3.79%`였지만, bootstrap 90% 구간이 `[-1.55%, 9.80%]`로 넓어서 안정적 양의 기대수익으로 단정할 수준은 아니다.

Simplifications made:
- Missing `trade_history.db` 때문에 기존 `scripts/backtest_signals.py` 재실행 대신, 남아 있는 markdown table + `pykrx` + `guardrail_sim.json` 조합으로 독립 검증 경로를 만들었다.
- 전략 로직이나 배포 동작은 건드리지 않고, 분석/보고 surface만 추가했다.

Remaining risks:
- `trade_history.db`가 없어 `910a331` 스크립트의 원본 DB replay는 현 워크트리에서 검증하지 못했다.
- Throughput 비교와 profitability 비교는 서로 다른 표본면을 사용한다.
- Newly admitted cohort의 T+5 개선은 소수 대형 승자에 민감해 표본 확장 전까지는 exploratory evidence로만 봐야 한다.
- 새 validation/reporting surface는 `guardrail_sim.json` schema drift나 report rendering 전체를 테스트로 잠그지는 않았다.

Rollback note:
- Added validation artifacts only. Remove the new validation script, report, plan, and test if this reporting surface should be rolled back.
