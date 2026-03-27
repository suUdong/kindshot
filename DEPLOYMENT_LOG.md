# Deployment Log

Kindshot 운영 배포 이력 기록용 문서.

## Rules

- 최신 배포를 문서 최상단에 추가
- 배포 단위마다 날짜, 대상 환경, 커밋/태그, 변경 요약, 검증, 롤백 방법 기록
- 장애/이슈가 있으면 결과와 후속 조치까지 남김

---

## Template

### YYYY-MM-DD HH:MM KST

- Environment:
- Branch:
- Commit:
- Deployer:
- Summary:
- Validation:
- Rollback:
- Result:
- Notes:

---

## Entries

### 2026-03-28 06:30 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `709cfd7`
- Deployer: Codex manual `rsync` + direct `ssh kindshot-server`
- Summary:
  1. **entry-quality runtime sync** — deployed the avg20d volume-ratio entry-quality slice so runtime context, guardrails, replay, and local analysis all share the same liquidity signal path
  2. **deterministic regression guard** — included the fixed-time pipeline test helper so shadow-snapshot assertions no longer drift with wall-clock time during validation
  3. **service restart + health sign-off** — recompiled the remote tree, reinstalled it with `./.venv/bin/python -m pip install -e . --quiet`, restarted `kindshot` and `kindshot-dashboard`, then confirmed `/health` and the dashboard were both healthy
- Validation:
  - local `python3 -m compileall src scripts tests dashboard`
  - local `.venv/bin/python scripts/entry_filter_analysis.py` → `logs/daily_analysis/entry_filter_analysis_20260328.txt`
  - local `.venv/bin/python -m pytest tests/test_guardrails.py tests/test_context_card.py tests/test_pipeline.py tests/test_entry_filter_analysis.py -q` → `232 passed`
  - local `.venv/bin/python -m pytest -q` → `1013 passed, 1 skipped, 1 warning`
  - local `python3 -m compileall tests/test_pipeline.py`
  - diagnostics `lsp_diagnostics_directory` → `0 errors`, `0 warnings`
  - `git push origin main` → `709cfd7` pushed to `origin/main`
  - remote `./.venv/bin/python -m compileall src/kindshot scripts tests dashboard`
  - remote `./.venv/bin/python -m pip install -e . --quiet`
  - remote `systemctl is-active kindshot kindshot-dashboard` → both `active`
  - remote `systemctl status kindshot kindshot-dashboard --no-pager -l` → both active since `2026-03-28 06:29:58 KST`
  - remote `/health` returned:
    - `status=healthy`
    - `started_at=2026-03-28T06:30:00.854671+09:00`
    - `last_poll_source=feed`
    - `last_poll_age_seconds=12`
    - `guardrail_state.configured_max_positions=4`
    - `trade_metrics.total_trades=0`
    - `recent_pattern_profile.total_trades=14`
  - remote dashboard probe `HEAD http://127.0.0.1:8501/` → `HTTP/1.1 200 OK`, `Content-Type: text/html`
- Rollback: re-sync the prior known-good runtime tree to `/opt/kindshot`, rerun `./.venv/bin/python -m pip install -e . --quiet`, and restart `kindshot` plus `kindshot-dashboard`
- Result: 성공
- Notes: the local shell still used direct `ssh`/`rsync` instead of the missing `ks` alias; this run kept `deploy/`, secrets, `.env`, and live-order behavior unchanged

---

### 2026-03-28 04:27 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `7612ec2` + local worktree sync (`scripts/entry_filter_analysis.py`, `tests/test_context_card.py`)
- Deployer: Codex manual `rsync` + direct `ssh kindshot-server`
- Summary:
  1. **latest runtime/reporting tree sync** — re-synced the validated local `src/`, `dashboard/`, `scripts/`, `tests/`, `config/`, and package metadata so the server reflects the current backtest, entry, exit, and performance-related code paths
  2. **remote venv recovery install** — remote `./.venv/bin/pip` failed because its shebang still pointed at `.venv.new`, so the deploy completed with `./.venv/bin/python -m pip install -e . --quiet`
  3. **service restart + health sign-off** — restarted both `kindshot` and `kindshot-dashboard`, confirmed `/health` returned `healthy`, and verified the dashboard served `200 text/html`
- Validation:
  - local `python3 -m compileall src scripts tests`
  - local `.venv/bin/python -m pytest tests/test_decision.py tests/test_health.py tests/test_pipeline.py tests/test_context_card.py tests/test_trade_db.py tests/test_monthly_full_strategy_backtest.py -q` → `140 passed, 1 warning`
  - local `.venv/bin/python scripts/runtime_latency_report.py`
  - local `.venv/bin/python scripts/entry_filter_analysis.py`
  - local `.venv/bin/python scripts/monthly_full_strategy_backtest.py`
  - local `.venv/bin/python -m pytest -q` → `1001 passed, 1 skipped, 1 warning`
  - diagnostics `lsp_diagnostics_directory` → `0 errors`, `0 warnings`
  - remote `./.venv/bin/python -m compileall src/kindshot scripts tests dashboard`
  - remote `./.venv/bin/python -m pip install -e . --quiet`
  - remote `systemctl is-active kindshot kindshot-dashboard` → both `active`
  - remote `systemctl status kindshot kindshot-dashboard --no-pager -l` → both active since `2026-03-28 04:27:18 KST`
  - remote `journalctl -u kindshot -n 20 --no-pager` showed:
    - `kindshot 0.1.3 starting`
    - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=14 boost=1 loss=2`
    - `Health server started on 127.0.0.1:8080`
  - remote `/health` returned:
    - `status=healthy`
    - `started_at=2026-03-28T04:27:20.859306+09:00`
    - `configured_max_positions=4`
    - `trade_metrics.total_trades=0`
    - `trade_metrics.total_pnl_pct=0.0`
  - remote dashboard probe `GET http://127.0.0.1:8501/` → `200 text/html`
- Rollback: re-sync the prior known-good runtime tree to `/opt/kindshot`, rerun `./.venv/bin/python -m pip install -e . --quiet`, and restart `kindshot` plus `kindshot-dashboard`
- Result: 성공
- Notes: local shell did not have the `ks` alias, so this deploy used direct `ssh`/`rsync`; the server remains in VTS-backed paper mode, so fresh intraday entry/exit latency evidence still requires the next live session

---

### 2026-03-28 03:52 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `3422df4`, `95c740d`
- Deployer: Codex clean-export `git archive HEAD` + `rsync --relative`
- Summary:
  1. **stale-entry cutoff** — deployed the new `ENTRY_DELAY_TOO_LATE` guardrail with `MAX_ENTRY_DELAY_MS=60000` so stale BUY setups stop before paper execution
  2. **depth/liquidity hardening** — deployed `ORDERBOOK_IMBALANCE`, the stronger `MIN_INTRADAY_VALUE_VS_ADV20D=0.15` floor, and the post-10:00 `PRIOR_VOLUME_TOO_THIN` gate
  3. **runtime helper hotfix** — followed the main entry-filter rollout with `95c740d` to ship `src/kindshot/entry_filter_analysis.py` after the first remote restart exposed the missing helper import
- Validation:
  - local `python3 -m compileall src scripts tests`
  - local `.venv/bin/python -m pytest tests/test_guardrails.py tests/test_context_card.py tests/test_pipeline.py tests/test_entry_filter_analysis.py -q` → `213 passed`
  - local `.venv/bin/python scripts/entry_filter_analysis.py` → delay kept `12 / 14` at avg `-0.073%`, delay late `2 / 14` at avg `-0.602%`; liquidity kept `5 / 14` at avg `+0.071%`; orderbook runtime coverage still sparse
  - local `.venv/bin/python -m pytest -q` → `988 passed, 1 skipped, 1 warning`
  - diagnostics `lsp_diagnostics_directory` → `0 errors`, `0 warnings`
  - `git push origin main` → `Everything up-to-date`
  - remote `./.venv/bin/python -m compileall src/kindshot scripts/entry_filter_analysis.py`
  - remote `./.venv/bin/python -m pip install -e . --quiet`
  - remote `systemctl is-active kindshot` → `active`
  - remote `systemctl is-active kindshot-dashboard` → `active`
  - remote `systemctl status kindshot --no-pager -l` → active since `2026-03-28 03:52:08 KST`, `ExecStart=/opt/kindshot/.venv/bin/python -m kindshot --paper`
  - remote `journalctl -u kindshot -n 20 --no-pager` showed:
    - `kindshot 0.1.3 starting`
    - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=14 boost=1 loss=2`
    - `Health server started on 127.0.0.1:8080`
  - remote `curl -fsS http://127.0.0.1:8080/health` → `status=healthy`, `started_at=2026-03-28T03:52:10.362041+09:00`, `configured_max_positions=4`, `position_count=0`
- Rollback: re-sync the prior known-good `src/kindshot/{config.py,guardrails.py,pipeline.py,entry_filter_analysis.py}` plus `scripts/entry_filter_analysis.py` to `/opt/kindshot`, reinstall with the remote venv, and restart `kindshot`
- Result: 성공
- Notes: 재시작 직후 첫 `/health` 호출은 포트 warm-up 전에 `connection refused` 였지만, 3초 후 재시도에서 정상 `healthy` 로 수렴

---

### 2026-03-28 03:38 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `f1f583d`
- Deployer: Codex manual SSH + `rsync`
- Summary:
  1. **support composite 보정** — support reference가 최근 5일 저점으로 붕괴되지 않도록, 최근 5세션 저점과 그 이전 20세션 구간 저점을 분리해 stronger floor를 선택
  2. **partial target ratio 복원** — `PARTIAL_TAKE_PROFIT_TARGET_RATIO`를 다시 런타임에 연결하고 기본값을 `1.0`으로 맞춰 target-hit partial semantics와 설정 의미를 일치시킴
  3. **문서/설정 정합성 회복** — exit 설계 문서와 config validation을 실제 런타임 동작과 다시 동기화
- Validation:
  - local `python3 -m compileall src tests`
  - local `.venv/bin/python -m pytest tests/test_config.py tests/test_context_card.py tests/test_price.py tests/test_pipeline.py tests/test_performance.py tests/test_telegram_ops.py -q` → `130 passed, 1 skipped`
  - local `.venv/bin/python -m pytest -q` → `981 passed, 1 skipped, 1 warning`
  - diagnostics `lsp_diagnostics_directory` → `0 errors`, `0 warnings`
  - remote `./.venv/bin/python -m compileall src/kindshot`
  - remote `./.venv/bin/python -m pip install -e . --quiet`
  - remote `systemctl is-active kindshot` → `active`
  - remote `systemctl status kindshot --no-pager -l` → active since `2026-03-28 03:37:57 KST`, `ExecStart=/opt/kindshot/.venv/bin/python -m kindshot --paper`
  - remote `curl -fsS http://127.0.0.1:8080/health` → `status=healthy`, `started_at=2026-03-28T03:37:59.058179+09:00`
- Rollback: re-sync the prior known-good `src/kindshot/{config.py,context_card.py,price.py}` slice to `/opt/kindshot/src/`, reinstall with the remote venv, and restart `kindshot`
- Result: 성공
- Notes: 재시작 직후 첫 `/health` 조회는 포트 warm-up 전에 실패했지만, 수 초 후 재확인에서 정상 `healthy`로 수렴

---

### 2026-03-28 03:12 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `2d06abd`
- Deployer: Codex manual SSH + `rsync -R` (`src/kindshot/decision.py`, `src/kindshot/pipeline.py`, `src/kindshot/prompts/decision_strategy.txt`, `scripts/llm_prompt_eval.py`) + remote venv reinstall + `kindshot` restart
- Summary:
  1. **LLM optimization runtime sync** — re-synced the prompt-optimization runtime slice (`decision.py`, `pipeline.py`, `decision_strategy.txt`, `llm_prompt_eval.py`) to `/opt/kindshot` using the established non-git rsync lane
  2. **service restart** — recompiled the remote runtime, reinstalled the package into the remote venv, and restarted `kindshot` under systemd
  3. **health confirmation** — verified the post-restart service state, recent journal, and `/health` payload remained healthy with the expected guardrail and pattern-profile fields
- Validation:
  - local baseline from the immediately preceding validated run: `.venv/bin/python -m pytest -q` → `977 passed, 1 skipped, 1 warning`
  - remote `./.venv/bin/python -m compileall src/kindshot scripts`
  - remote `./.venv/bin/python -m pip install -e . --quiet`
  - remote `systemctl is-active kindshot` → `active`
  - remote `systemctl status kindshot --no-pager -l` showed:
    - `Active: active (running) since Sat 2026-03-28 03:11:19 KST`
    - `ExecStart=/opt/kindshot/.venv/bin/python -m kindshot --paper`
  - remote `journalctl -u kindshot -n 20 --no-pager` showed:
    - `kindshot 0.1.3 starting`
    - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=14 boost=1 loss=2`
    - `Health server started on 127.0.0.1:8080`
  - remote `curl -sf http://127.0.0.1:8080/health` returned:
    - `status: "healthy"`
    - `started_at: "2026-03-28T03:11:20.479452+09:00"`
    - `last_poll_source: "feed"`
    - `last_poll_age_seconds: 0`
    - `guardrail_state.configured_max_positions: 4`
    - `recent_pattern_profile.total_trades: 14`
- Rollback: re-sync the prior known-good runtime files for this slice (for example from the previous validated main tree), reinstall with the remote venv, and restart `kindshot`
- Result: 성공
- Notes: this was a deploy-only run on a clean local tree, so local test evidence was reused from the immediately preceding validated LLM optimization run while fresh remote compile/restart/health evidence was collected in this deployment; the `Commit` field reflects the local HEAD at deploy time, while the deployed runtime slice itself was last fully runtime-validated in the prior `425c07d` entry

### 2026-03-28 03:05 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `425c07d`
- Deployer: Codex manual SSH + `rsync -R` (`src/kindshot/decision.py`, `src/kindshot/pipeline.py`, `src/kindshot/prompts/decision_strategy.txt`, `scripts/llm_prompt_eval.py`) + `kindshot` restart
- Summary:
  1. **offline prompt-eval surface** — added `scripts/llm_prompt_eval.py` so local history can report current LLM decision accuracy, confidence calibration, and fast-profile late-entry call-avoidance candidates from the same recorded evidence window
  2. **confidence/prompt tightening** — updated `decision_strategy.txt` to treat confidence as the correctness probability of the chosen action over the actual hold profile and to suppress routine `hold_profile=20m` contract overconfidence around the open/late tape
  3. **LLM cost reduction without new trading risk** — moved `FAST_PROFILE_LATE_ENTRY` blocking ahead of the LLM call so non-executable 20-minute late entries stop consuming provider calls while preserving the same operator-facing guardrail outcome
- Validation:
  - local `python3 -m compileall src scripts tests dashboard`
  - local `.venv/bin/python -m pytest tests/test_llm_prompt_eval.py tests/test_pipeline.py tests/test_decision.py -q` → `87 passed`
  - local `.venv/bin/python -m pytest -q` → `977 passed, 1 skipped, 1 warning`
  - local changed-file diagnostics on `src/kindshot/decision.py`, `src/kindshot/pipeline.py`, `scripts/llm_prompt_eval.py`, `tests/test_pipeline.py`, `tests/test_llm_prompt_eval.py` → `0 errors`
  - local prompt-eval artifact `logs/daily_analysis/llm_prompt_eval_20260328.{txt,json}` recorded:
    - balanced sample: `16` cases (`8 BUY target`, `8 SKIP target`)
    - historical actual: `accuracy=0.625`
    - `buy_precision=1.0`
    - `skip_precision=0.5714`
    - `buy_recall=0.25`
    - `false_negative_rate=0.75`
    - fast-profile late cost candidates: `8 / 10`
    - live prompt replay blocked by Anthropic provider credit error (`invalid_request_error: credit balance is too low`)
  - remote `python3 -m compileall src/kindshot scripts`
  - remote `systemctl is-active kindshot` → `active`
  - remote `curl -sf http://127.0.0.1:8080/health` returned:
    - `status: "healthy"`
    - `last_poll_source: "feed"`
    - `last_poll_age_seconds: 11`
    - `guardrail_state.configured_max_positions: 4`
    - `recent_pattern_profile.total_trades: 14`
  - remote journal after restart showed:
    - `kindshot 0.1.3 starting`
    - `Health server started on 127.0.0.1:8080`
    - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=14 boost=1 loss=2`
- Rollback: re-sync the prior runtime files from `6d1a3f4` (or revert `425c07d`), then restart `kindshot`
- Result: 성공
- Notes: prompt A/B replay tooling is now in place, but the actual live-model variant replay is currently blocked by the Anthropic account credit error on this host; the baseline historical measurement still completed and the runtime cost reduction shipped independently of that blocker

### 2026-03-28 02:28 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `6d1a3f4`
- Deployer: Codex manual SSH + `rsync` (`dashboard/app.py`, `dashboard/data_loader.py`) + `kindshot-dashboard` restart
- Summary:
  1. **final-check dashboard warning cleanup** — replaced deprecated Streamlit `use_container_width` usage with `width="stretch"` and normalized multi-day concat inputs to remove dashboard smoke-test warning noise without changing trading behavior
  2. **dashboard-only deploy** — re-synced the two dashboard files to `/opt/kindshot/dashboard`, recompiled them remotely, and restarted only `kindshot-dashboard`
  3. **final sign-off evidence** — re-ran remote dashboard AppTest with `-W error::FutureWarning`, confirmed all six tabs render with `exception_count=0`, and preserved backend health / prior-trading-day pipeline evidence
- Validation:
  - local `python3 -m compileall src scripts tests dashboard`
  - local `.venv/bin/python -m pytest tests/test_dashboard.py -q` → `22 passed`
  - local `.venv/bin/python -m pytest -q` → `974 passed, 1 skipped, 1 warning`
  - local changed-file diagnostics on `dashboard/app.py`, `dashboard/data_loader.py` → `0 errors`
  - remote `python3 -m compileall dashboard`
  - remote `systemctl is-active kindshot-dashboard` → `active`
  - remote `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
  - remote AppTest (`PYTHONPATH=. ../.venv/bin/python -W error::FutureWarning`) returned:
    - `tab_labels`: `📊 시그널 현황`, `💰 매매 성과`, `📉 기술지표`, `🖥️ 시스템 상태`, `🔬 전략 분석`, `📈 히스토리 분석`
    - `exception_count: 0`
  - remote `/health` after deploy returned:
    - `status: "healthy"`
    - `last_poll_source: "feed"`
    - `last_poll_age_seconds: 8`
    - `guardrail_state.configured_max_positions: 4`
    - `guardrail_state.recent_win_rate_multiplier: 1.0`
    - `recent_pattern_profile.total_trades: 14`
  - remote `logs/kindshot_20260327.jsonl` showed end-to-end prior-session evidence:
    - `event: 789`
    - `decision: 37`
    - `price_snapshot: 1242`
    - executed BUY sample count: `5`
    - guardrail-blocked BUY sample count from `guardrail_results`: `19`
  - remote `logs/polling_trace_20260328.jsonl` showed active feed polling on `2026-03-28` with recent `poll_start/poll_end` cycles and `last_time_after=235650`
- Rollback: re-sync the prior dashboard files from `5ea0269` (or redeploy the previous known-good tree to `/opt/kindshot/dashboard/`) and restart `kindshot-dashboard`
- Result: 성공
- Notes: `2026-03-28` is a Saturday in KST, so fresh same-day market events were unavailable; final end-to-end sign-off therefore uses live poll/health evidence plus the most recent trading-day log chain from `2026-03-27`

### 2026-03-28 02:03 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `5ea0269`
- Deployer: Codex manual SSH + `rsync` (runtime files + `config/`) + remote venv reinstall
- Summary:
  1. **max position cap rollout** — synced the new checked-in `config/risk_limits.toml` plus updated runtime modules so `MAX_POSITIONS=9999` no longer disables the paper-trading simultaneous-position guardrail
  2. **service restart** — restarted both `kindshot` and `kindshot-dashboard` under systemd and confirmed both units returned to `active`
  3. **runtime verification** — confirmed both remote `Config().max_positions` and `/health.guardrail_state.configured_max_positions` resolve to `4`
- Validation:
  - local `python3 -m compileall src scripts tests dashboard`
  - local `.venv/bin/python -m pytest tests/test_config.py tests/test_guardrails.py tests/test_health.py tests/test_pipeline.py -q` → `216 passed, 1 warning`
  - local `.venv/bin/python -m pytest -q` → `974 passed, 1 skipped, 1 warning`
  - remote `python3 -m compileall src scripts tests dashboard`
  - remote `./.venv/bin/python -m pip install . --quiet`
  - remote `systemctl is-active kindshot kindshot-dashboard` → both `active`
  - remote `./.venv/bin/python -c 'from kindshot.config import Config; print(Config().max_positions)'` → `4`
  - remote health summary returned:
    - `status: "healthy"`
    - `guardrail_state.configured_max_positions: 4`
    - `guardrail_state.position_count: 0`
    - `guardrail_state.dynamic_daily_loss_floor_won: -3000000.0`
    - `guardrail_state.recent_closed_trades: 0`
    - `guardrail_state.consecutive_loss_halt_threshold: 3`
    - `guardrail_state.sector_positions: {}`
    - `recent_pattern_profile.total_trades: 14`
  - remote `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
  - remote journal after restart showed:
    - `kindshot 0.1.3 starting`
    - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=14 boost=1 loss=2`
    - `Health server started on 127.0.0.1:8080`
- Rollback: redeploy the prior known-good tree (or revert `5ea0269`), reinstall with the remote venv, and restart `kindshot` + `kindshot-dashboard`
- Result: 성공
- Notes: the first immediate post-restart health probe returned an empty body during service warm-up, but the follow-up probe passed once the health server finished binding

### 2026-03-28 01:42 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `2bda06d` (runtime code for risk v2 remains `839ffdc`)
- Deployer: Codex manual SSH + `rsync` (tracked runtime files) + remote venv reinstall
- Summary:
  1. **latest tracked runtime sync** — re-synced `src/`, `dashboard/`, `scripts/`, `tests/`, `pyproject.toml`, `README.md`, and `requirements.lock` to `/opt/kindshot` so the server matches the latest local runtime-relevant tree without mutating remote git metadata
  2. **service restart** — restarted both `kindshot` and `kindshot-dashboard` under systemd and confirmed both units returned to `active`
  3. **risk v2 runtime confirmation** — `/health.guardrail_state` still reports the risk management v2 fields (`recent_closed_trades`, `recent_win_rate_multiplier`, `consecutive_loss_halt_threshold`, `sector_positions`) and the recent pattern profile remained loaded after restart
- Validation:
  - local `python3 -m compileall src scripts tests dashboard`
  - local `.venv/bin/python -m pytest -q` → `971 passed, 1 skipped, 1 warning`
  - remote `python3 -m compileall src scripts tests dashboard`
  - remote `./.venv/bin/python -m pip install . --quiet`
  - remote `systemctl is-active kindshot kindshot-dashboard` → both `active`
  - remote health summary returned:
    - `status: "healthy"`
    - `guardrail_state.dynamic_daily_loss_floor_won: -3000000.0`
    - `guardrail_state.dynamic_daily_loss_remaining_won: 3000000.0`
    - `guardrail_state.recent_closed_trades: 0`
    - `guardrail_state.recent_win_rate: null`
    - `guardrail_state.recent_win_rate_multiplier: 1.0`
    - `guardrail_state.consecutive_loss_halt_threshold: 3`
    - `guardrail_state.sector_positions: {}`
    - `recent_pattern_profile.total_trades: 14`
  - remote `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
  - remote journal after restart showed:
    - `kindshot 0.1.3 starting`
    - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=14 boost=1 loss=2`
    - `Health server started on 127.0.0.1:8080`
- Rollback: re-sync the prior known-good tree to `/opt/kindshot`, reinstall with the remote venv, and restart `kindshot` + `kindshot-dashboard`
- Result: 성공
- Notes: remote `/opt/kindshot` still has no useful git HEAD metadata, so file sync plus live service checks remain the deployment source of truth

### 2026-03-28 01:33 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `839ffdc`
- Deployer: Codex manual SSH + `rsync` (`src/`, `tests/`) + remote venv reinstall
- Summary:
  1. **recent win-rate based daily loss tightening** — `GuardrailState` now persists same-day closed-trade outcomes and `resolve_daily_loss_budget()` tightens the effective floor when recent win rate deteriorates, without expanding above the configured base limit
  2. **runtime-effective sector concentration** — KIS quote context now carries sector metadata (`bstp_kor_isnm`), pipeline BUY bookkeeping records sector state, and SELL bookkeeping recovers persisted ticker→sector mappings so sector counts survive final close and restarts
  3. **risk observability** — `/health.guardrail_state` now exposes recent closed-trade count, recent win rate, recent win-rate multiplier, sector positions, and the configured consecutive-loss halt threshold
- Validation:
  - local `python3 -m compileall src scripts tests dashboard`
  - local `.venv/bin/python -m pytest tests/test_guardrails.py tests/test_pipeline.py tests/test_health.py tests/test_config.py -q` → `213 passed, 1 warning`
  - local `.venv/bin/python -m pytest -q` → `971 passed, 1 skipped, 1 warning`
  - local affected-file diagnostics → `0 errors`, `0 warnings`
  - remote `python3 -m compileall src/kindshot scripts tests dashboard`
  - remote `.venv/bin/python -m pip install . --quiet`
  - remote `sudo systemctl restart kindshot kindshot-dashboard`
  - remote `systemctl is-active kindshot kindshot-dashboard` → both `active`
  - remote health summary returned:
    - `status: "healthy"`
    - `guardrail_state.dynamic_daily_loss_floor_won: -3000000.0`
    - `guardrail_state.recent_closed_trades: 0`
    - `guardrail_state.recent_win_rate_multiplier: 1.0`
    - `guardrail_state.consecutive_loss_halt_threshold: 3`
    - `guardrail_state.sector_positions: {}`
  - remote `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
  - remote journal after restart showed:
    - `kindshot 0.1.3 starting`
    - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=14 boost=1 loss=2`
    - `Health server started on 127.0.0.1:8080`
- Rollback: re-sync the prior known-good tree to `/opt/kindshot`, reinstall with the remote venv, and restart `kindshot` + `kindshot-dashboard`
- Result: 성공
- Notes: first remote install attempt used `python -m pip` and failed because `python` was not on the service shell `PATH`; retrying with `.venv/bin/python -m pip` succeeded without further changes

---

### 2026-03-28 01:08 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `c0c42e2`
- Deployer: Codex manual SSH + clean `git archive` export + `rsync`
- Summary:
  1. **recent pattern profile alignment 배포** — runtime `RecentPatternProfile` 이 raw DB fallback 전에 `scripts/backtest_analysis.py` reconstruction path 를 사용하도록 정렬
  2. **recent window 확대 + summary persistence** — recent-pattern lookback 기본값을 `6 → 7` log days 로 늘리고, profile summary 를 `recent_pattern_profile_path` 에 기록
  3. **runtime verification** — `kindshot` / `kindshot-dashboard` 재시작 후 health payload 에 recent-pattern summary 가 나타나는지 확인
- Validation:
  - local `python3 -m compileall src scripts tests dashboard`
  - local `.venv/bin/python -m pytest tests/test_strategy_observability.py tests/test_pattern_profile.py tests/test_backtest_analysis.py tests/test_pipeline.py tests/test_config.py -q` → `54 passed`
  - local `.venv/bin/python -m pytest -x -q` → `963 passed, 1 skipped, 1 warning`
  - local affected-file diagnostics → `0 errors`, `0 warnings`
  - remote `python3 -m compileall src/kindshot scripts tests dashboard`
  - remote `source .venv/bin/activate && python -m pip install . --quiet`
  - remote `sudo systemctl restart kindshot kindshot-dashboard`
  - remote `systemctl is-active kindshot kindshot-dashboard` → both `active`
  - remote `curl -sf http://127.0.0.1:8080/health` returned:
    - `recent_pattern_profile.enabled: true`
    - `recent_pattern_profile.analysis_dates: ['20260319', '20260320', '20260327']`
    - `recent_pattern_profile.loss_guardrail_patterns: 2`
    - `recent_pattern_profile.top_profit_exact.key: "mna|005380|midday"`
  - remote `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
  - remote journal after restart showed:
    - `Backfilled 20260318/19/20/23/26/27 BUY trades`
    - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=12 boost=0 loss=2`
    - `Health server started on 127.0.0.1:8080`
- Rollback: re-sync the prior known-good tree to `/opt/kindshot`, reinstall with the remote venv, and restart `kindshot` + `kindshot-dashboard`
- Result: 성공
- Notes: first post-restart health/dashboard probe hit warm-up timing and saw connection refused, but follow-up checks passed once services finished binding

---

### 2026-03-28 00:21 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `f0e1bc4`
- Deployer: Codex manual SSH + clean `git archive` export + `rsync`
- Summary:
  1. **health heartbeat 정합화 배포** — `/health.last_poll_at` 가 internal timestamp 대신 runtime feed heartbeat source 를 사용하도록 `health.py`/`main.py` 를 반영
  2. **실시간 trade metrics 노출** — runtime 이 closed-trade 기준 `trade_metrics` (`win_rate`, `total_pnl_pct`, `total_pnl_won`, `avg_pnl_pct`, `peak_ret_pct`, `mdd_pct`) 를 health payload 로 제공
  3. **dashboard live observability 반영** — Streamlit 대시보드가 health payload 의 `trade_metrics`, `last_poll_source`, `last_poll_age_seconds` 를 읽어 실시간 KPI/heartbeat 상태를 표시
- Validation:
  - local `python3 -m compileall src dashboard tests`
  - local `.venv/bin/python -m pytest tests/test_health.py tests/test_performance.py tests/test_dashboard.py -q` → `39 passed`
  - local `.venv/bin/python -m pytest -q` → `956 passed, 1 skipped`
  - local affected-file diagnostics → `0 errors`
  - remote `python3 -m compileall src/kindshot dashboard tests`
  - remote `source .venv/bin/activate && python -m pip install . --quiet`
  - remote `systemctl is-active kindshot kindshot-dashboard` → both `active`
  - remote `curl -sf http://127.0.0.1:8080/health` returned:
    - `last_poll_source: "feed"`
    - `trade_metrics` block present with zeroed live metrics before market activity
  - remote `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
  - remote journal after restart:
    - `kindshot 0.1.3 starting`
    - `Health server started on 127.0.0.1:8080`
    - later heartbeat `last_poll=00:20:14`
  - follow-up remote health check returned `last_poll_at=2026-03-28T00:20:31.008898+09:00`, `last_poll_source=feed`, `last_poll_age_seconds=3`
  - remote source files contained new strings:
    - `trade_metrics`
    - `last_poll_source`
    - `실시간 트레이딩 메트릭`
- Rollback: re-sync the prior known-good tree to `/opt/kindshot`, reinstall with the remote venv, and restart `kindshot` + `kindshot-dashboard`
- Result: 성공
- Notes: initial non-sudo `systemctl restart` failed with interactive auth, but `sudo systemctl restart ...` succeeded; the deployed services now report the new health payload shape

### 2026-03-28 00:05 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: clean export from local `25d339c`
- Deployer: Codex manual SSH + `rsync`
- Summary:
  1. **clean HEAD export redeploy** — local worktree had an unrelated dirty `guardrails.py`, so deployment used a clean `git archive` snapshot from pushed `25d339c`
  2. **runtime sync + reinstall** — `src/`, `dashboard/`, `tests/`, `scripts/`, and package metadata were rsynced to `/opt/kindshot`, then remote `compileall` + `pip install . --quiet` completed successfully
  3. **service restart + v69 smoke verification** — `kindshot` and `kindshot-dashboard` restarted cleanly, `/health` and dashboard HTTP returned healthy/200, and remote smoke checks confirmed prompt enrichment, dynamic daily loss floor wiring, and tighter post-partial trailing behavior
- Validation:
  - remote `python3 -m compileall src/kindshot scripts tests`
  - remote `source .venv/bin/activate && python -m pip install . --quiet`
  - remote `systemctl is-active kindshot kindshot-dashboard` → both `active`
  - remote `curl http://127.0.0.1:8080/health` → `healthy`
  - remote `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
  - remote sha256 matched clean export for:
    - `src/kindshot/decision.py`
    - `src/kindshot/price.py`
    - `src/kindshot/guardrails.py`
    - `src/kindshot/main.py`
    - `src/kindshot/health.py`
    - `src/kindshot/prompts/decision_strategy.txt`
  - remote smoke script passed:
    - `ctx_signal` / `ctx_risk` prompt fields present
    - direct disclosure + contract amount fields rendered in prompt
    - dynamic daily loss floor matched live config formula and health payload
    - post-partial trailing stop was tighter than pre-partial trailing
- Rollback: re-sync the prior known-good tree to `/opt/kindshot`, reinstall with the remote venv, and restart `kindshot` + `kindshot-dashboard`
- Result: 성공
- Notes: `/health` remained healthy after restart; runtime is still in VTS price mode because real quote keys are not present, so live-day observation is still needed for full intraday exit calibration

### 2026-03-27 23:46 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: clean export from local `846cfd5` (`main` already ahead of the original v69 slice)
- Deployer: Codex manual SSH + `rsync`
- Summary:
  1. **clean export overwrite** — remote `/opt/kindshot` worktree was dirty and stale, so deployment used a tracked-files-only export instead of `git pull`
  2. **v69 runtime path 반영 확인** — `decision.py`, `price.py`, `guardrails.py`, `main.py`, `decision_strategy.txt` hash가 로컬과 원격에서 일치함을 확인
  3. **서비스 재기동 및 운영 검증** — `kindshot`, `kindshot-dashboard` 재시작 후 `/health`와 dashboard HTTP `200` 확인
- Validation:
  - local `python3 -m compileall src/kindshot tests scripts`
  - local `.venv/bin/python -m pytest tests/test_decision.py tests/test_guardrails.py tests/test_price.py tests/test_performance.py tests/test_pipeline.py tests/test_telegram_ops.py -q` → `285 passed, 1 skipped`
  - local `.venv/bin/python -m pytest -q` → `934 passed, 1 skipped, 1 warning`
  - local affected-file diagnostics → `0 errors`, `0 warnings`
  - remote `python3 -m compileall src/kindshot scripts tests`
  - remote `python -m pip install . --quiet`
  - remote `systemctl is-active kindshot kindshot-dashboard` → both `active`
  - remote `curl http://127.0.0.1:8080/health` → `healthy`
  - remote `curl http://127.0.0.1:8501` → `200`
  - remote sha256 matched local for:
    - `src/kindshot/decision.py`
    - `src/kindshot/price.py`
    - `src/kindshot/guardrails.py`
    - `src/kindshot/main.py`
    - `src/kindshot/prompts/decision_strategy.txt`
- Rollback: re-sync the prior known-good tree to `/opt/kindshot`, then restart `kindshot` and `kindshot-dashboard`
- Result: 성공
- Notes: remote `git rev-parse HEAD` still reports the older commit because this deployment intentionally avoided mutating remote git metadata; file hashes and running-service checks are the deployment source of truth

### 2026-03-27 17:44 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `a2a6f8a` deployed via clean export overwrite
- Deployer: Codex manual SSH + `rsync`
- Summary:
  1. **clean export 배포** — 로컬/원격 worktree가 모두 dirty여서 `git pull` 대신 `git archive a2a6f8a` 산출물만 `/opt/kindshot`에 `rsync` 동기화
  2. **a2a6f8a 반영 확인** — `scripts/auto_tune_strategy.py`, `scripts/backtest_analysis.py`, `tests/test_auto_tune_strategy.py`, `tests/test_backtest_analysis.py`를 서버에 반영하고 해시 일치 검증
  3. **서비스 재기동 및 운영 검증** — `kindshot`, `kindshot-dashboard` 재시작 후 `/health`, `journalctl`, 대시보드 HTTP 응답 확인
- Validation:
  - remote `pip install -e . --quiet`
  - remote `systemctl is-active kindshot kindshot-dashboard` → both `active`
  - remote `curl http://127.0.0.1:8080/health` → `{"status":"healthy", ...}`
  - remote recent/live journal confirmed `kindshot 0.1.3 starting`, `Health server started on 127.0.0.1:8080`, Streamlit `Local URL: http://localhost:8501`
  - remote `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
  - local SSH tunnel `18501 -> remote 8501` then `curl -I http://127.0.0.1:18501` → `HTTP/1.1 200 OK`
  - remote sha256 matched clean export for:
    - `scripts/auto_tune_strategy.py`
    - `scripts/backtest_analysis.py`
    - `tests/test_auto_tune_strategy.py`
    - `tests/test_backtest_analysis.py`
- Rollback: re-sync the prior known-good tree to `/opt/kindshot` and restart `kindshot` + `kindshot-dashboard`, or restore the previously deployed files from the remote worktree backup/source of truth
- Result: 성공
- Notes: direct public `http://3.35.14.35:8501` from this environment timed out, but remote local access and SSH-tunneled access both returned `200 OK`; likely external network policy or path-specific reachability issue outside the app process itself

### 2026-03-27 17:19 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `fa47b64`
- Deployer: Codex manual SSH + `rsync`
- Summary:
  1. **대시보드 관측면 확장** — 당일 equity curve / drawdown, 주간 cumulative curve / drawdown, live news feed monitor 추가
  2. **shadow snapshot 가시화** — 차단 BUY 가상 수익률 KPI/테이블과 version trend(v64/v65/v66) 비교 표면 추가
  3. **모바일 가독성 강화** — Streamlit metric/table/card spacing과 responsive CSS 정리
- Validation:
  - local `python3 -m compileall dashboard tests scripts src`
  - local `.venv/bin/python -m pytest tests/test_dashboard.py -q` → `21 passed`
  - local `.venv/bin/python -m pytest -q` → `820 passed, 1 skipped, 1 warning`
  - remote `/opt/kindshot/dashboard/app.py` contains `compute_daily_equity_curve`, `load_shadow_trade_pnl`, `load_version_trend`, `실시간 뉴스 피드 모니터`
  - remote `systemctl is-active kindshot-dashboard` → `active`
  - remote `curl http://127.0.0.1:8080/health` → `healthy`
  - remote `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
- Rollback: re-sync prior known-good `dashboard/` tree to `/opt/kindshot/dashboard/` and restart `kindshot-dashboard`
- Result: 성공
- Notes: dashboard service needed a short warm-up after restart; first immediate `curl` failed before Streamlit finished binding `:8501`

---

### 2026-03-28 03:32 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `8492d13`
- Deployer: Codex manual SSH + `rsync`
- Summary:
  1. **뉴스 즉시 청산 추가** — `NEG_STRONG` 및 correction/withdrawal 이벤트가 같은 티커의 열린 포지션을 `news_exit` / `correction_exit`로 즉시 청산 요청
  2. **기술적 지지선 청산 추가** — buy 시점에 5일/20일 완료봉 저점을 기반으로 support reference를 저장하고, 이후 가격 스냅샷이 buffer 아래로 이탈하면 `support_breach`로 청산
  3. **부분 익절 의미 수정** — 목표가 도달 시 전량 익절 대신 50%만 `partial_take_profit` 처리하고 잔여 물량은 tighter trailing으로 관리
- Validation:
  - local `python3 -m compileall src tests`
  - local `.venv/bin/python -m pytest tests/test_context_card.py tests/test_price.py tests/test_pipeline.py tests/test_performance.py tests/test_telegram_ops.py -q` → `110 passed, 1 skipped`
  - local `.venv/bin/python -m pytest -q` → `981 passed, 1 skipped, 1 warning`
  - diagnostics `lsp_diagnostics_directory` → `0 errors`, `0 warnings`
  - remote `./.venv/bin/python -m compileall src/kindshot`
  - remote `./.venv/bin/python -m pip install -e . --quiet`
  - remote `systemctl is-active kindshot` → `active`
  - remote `systemctl status kindshot --no-pager -l` → active since `2026-03-28 03:32:00 KST`, `ExecStart=/opt/kindshot/.venv/bin/python -m kindshot --paper`
  - remote `journalctl -u kindshot -n 20 --no-pager` showed:
    - `kindshot 0.1.3 starting`
    - `RecentPatternProfile loaded: dates=20260319,20260320,20260327 trades=14 boost=1 loss=2`
    - `Health server started on 127.0.0.1:8080`
  - remote `curl -fsS http://127.0.0.1:8080/health` → `status=healthy`, `configured_max_positions=4`, `position_count=0`
- Rollback: re-sync the prior known-good `src/kindshot/{config.py,context_card.py,models.py,pipeline.py,price.py}` tree to `/opt/kindshot/src/`, reinstall with the remote venv, and restart `kindshot`
- Result: 성공
- Notes: 서버는 여전히 VTS 가격 모드라서 실시간 시세 기반 trailing/T5M/support 동작의 시장중 검증은 다음 거래일에 다시 확인해야 함

---

### 2026-03-27 16:45 KST

- Environment: AWS Lightsail (`kindshot-server`, paper mode)
- Branch: `main`
- Commit: `485238d` runtime changes deployed from local `73ed7cb`
- Deployer: Codex manual SSH + `rsync`
- Summary:
  1. **v66 confidence 개선 반영** — opening gate `80→82`, 11시대 부스트 `+2`, 12시대 감점 유지, graduated cap 보호 강화
  2. **뉴스 시그널 강화 반영** — `headline_parser` 기반 analysis headline normalization, 계약/기사형 preflight 강화, `rule_fallback:article_pattern` 및 disclosure-source `dorg` 판별 개선
  3. **운영 관측성 유지** — 차단 BUY shadow snapshot 코드 포함, 대시보드 서비스 재시작 및 HTTP 응답 확인
- Validation:
  - local `python3 -m compileall src/kindshot tests scripts`
  - local `.venv/bin/python -m pytest tests/test_headline_parser.py tests/test_decision.py tests/test_guardrails.py tests/test_pipeline.py -q` → `225 passed`
  - remote `pip install -e . --quiet`
  - remote `systemctl is-active kindshot kindshot-dashboard` → both `active`
  - remote `curl http://127.0.0.1:8080/health` → `healthy`
  - remote live journal after restart: `2026-03-27 16:45:18` `Normalized analysis headline [000660] ...`
  - remote dashboard `curl -I http://127.0.0.1:8501` → `HTTP/1.1 200 OK`
- Rollback: re-sync prior known-good tree or revert the v66/news-signal commits and restart `kindshot` + `kindshot-dashboard`
- Result: 성공
- Notes: remote git worktree was dirty, so deployment used `rsync` instead of `git pull`

---

### 2026-03-13 (배포 예정)

- Environment: AWS Lightsail (production, paper mode)
- Branch: `main`
- Commit: `54c3c86`
- Deployer: manual (SSH)
- Summary:
  1. **IGNORE 버킷 신설** — Bucket enum에 IGNORE 추가. 주총/감사보고서 제출/소유주식수 변동/배당락 등 노이즈 사전 필터링
  2. **100+ 키워드 보강** (클로드 리서치 실증 근거):
     - POS_STRONG: 어닝서프라이즈, FDA 승인, 기술수출 계약, 경영권 분쟁, 특별배당 등
     - NEG_STRONG: 어닝쇼크, 적자전환, 물적분할, 임상 실패, 비적정 감사의견, 경영권 분쟁 종료 등
     - POS_WEAK: 인적분할, 매출 증가, 중간배당, 행동주의 주주 등
     - NEG_WEAK: 매출 감소, 최대주주 변경, 임상 지연 등
  3. **버킷 우선순위** — NEG_STRONG > POS_STRONG > NEG_WEAK > POS_WEAK > IGNORE > UNKNOWN
  4. **효과**: 3/12 unknown 758건 → 209건 (72% 감소)
- Validation: `pytest -x -q` 182 passed, 3 skipped
- Rollback: `git revert 54c3c86`
- Result: (배포 후 기록)
- Notes: 리서치 근거 `docs/research/2026-03-13-unknown-bucket-research.md`

---

### 2026-03-12 10:15 KST

- Environment: AWS Lightsail (production, paper mode)
- Branch: `main` (codex/roadmap-loop-foundation merged)
- Commit: `f1d1038` (Harden KIS pipeline) + `5e6ac4b`, `decf7ec` (polling fixes)
- Deployer: manual (SSH)
- Summary:
  1. **KIS 폴링 윈도우 정지 버그 수정** — `last_time` 갱신을 dup check 이전으로 이동. seen_dup만 반복될 때 폴링 윈도우가 전진 안 하던 문제 해결
  2. **KIS news API from_time 제거** — `FID_INPUT_HOUR_1`이 해당 시간 "이후"가 아닌 "이전" 데이터를 반환하는 것으로 확인. 항상 빈 문자열로 최신 뉴스 수신, seen_ids로 중복 제거
  3. **KIS 파이프라인 강화** (codex) — kis_client 리팩터링, guardrails/context_card/decision 개선, 테스트 대폭 추가
  4. **CLAUDE.md/AGENTS.md에 KIS API 레퍼런스 추가** — 공식 예제 레포 및 파라미터 주의사항 문서화
- Validation: `pytest -x -q` 136 passed, 3 skipped
- Rollback: `git revert f1d1038 && git revert decf7ec && git revert 5e6ac4b`
- Result: 배포 진행 중
- Notes: 배포 후 polling_trace에서 `raw_max_time`이 현재 시각 근처로 오는지 확인 필요
