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
