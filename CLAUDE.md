# Kindshot - KRX News Day-Trading MVP

## Language
- 대화는 한국어로 진행

## Conventions
- Commit: `fix:`, `feat:`, `chore:` prefix. No emoji.
- Korean comments OK in domain logic (bucket keywords, etc.)
- Config defaults in `config.py`, override via env vars

## Workflow

### Always do
1. Read before edit
2. `pytest -x -q` before commit
3. Keep commits atomic and descriptive

### Superpowers usage by task type

**New feature / new module (2+ files):**
1. brainstorming -> requirements & edge cases
2. writing-plans -> implementation steps
3. test-driven-development -> tests first
4. code-review -> before merge

**Bugfix (root cause unclear):**
1. systematic-debugging -> structured investigation
2. verification-before-completion -> confirm fix

**Bugfix (root cause obvious, 1 file):**
- Fix directly, no skills needed

**Config/keyword/threshold change:**
- Fix directly, no skills needed

### Why: pay upfront, save overall
Skipping skills during implementation leads to subtle bugs (null data, wrong defaults,
cache key omissions) that cost more tokens to find and fix later.

## Deploy

### 서버 접속
- SSH alias: `ks` (= `ssh kindshot-server`)
- 서버 경로: `/opt/kindshot`
- systemd: `kindshot.service` (paper mode)
- systemd: `kindshot-dashboard.service` (Streamlit 대시보드, 포트 8501)

### 배포 방법
**방법 1 — rsync (GitHub 인증 불필요, 권장):**
```bash
rsync -avz --exclude='.venv' --exclude='data/' --exclude='logs/' --exclude='.env' --exclude='__pycache__' --exclude='.git' src/ kindshot-server:/opt/kindshot/src/
rsync -avz --exclude='__pycache__' tests/ kindshot-server:/opt/kindshot/tests/
ks "cd /opt/kindshot && source .venv/bin/activate && pip install -e . --quiet && sudo systemctl restart kindshot"
```

**방법 2 — git pull (GitHub 인증 필요):**
```bash
# Push to main -> SSH to Lightsail
ks "cd /opt/kindshot && bash deploy/deploy.sh"
```

### 대시보드 배포
```bash
rsync -avz --exclude='__pycache__' dashboard/ kindshot-server:/opt/kindshot/dashboard/
ks "sudo systemctl restart kindshot-dashboard"
```

### 대시보드 접근 (SSH 터널)
```bash
# 로컬에서 SSH 터널 → http://localhost:8501 접속
ssh -L 8501:localhost:8501 kindshot-server
```

### 배포 확인
```bash
ks "sudo systemctl status kindshot --no-pager"
ks "sudo systemctl status kindshot-dashboard --no-pager"
ks "journalctl -u kindshot -n 20 --no-pager"
```

## KIS API Reference
- 공식 예제 레포: https://github.com/koreainvestment/open-trading-api
- LLM용 예제: `examples_llm/domestic_stock/` 하위 API별 폴더
- KIS API 파라미터 동작이 불확실할 때 위 레포의 예제를 반드시 참조할 것
- 주요 주의사항:
  - `FID_INPUT_HOUR_1`: 빈 문자열 = 현재 기준 최신, 값 입력 시 해당 시간 **이전** 데이터 반환
  - `FID_INPUT_DATE_1`: 빈 문자열 = 현재 기준, 포맷 `00YYYYMMDD`
  - 페이지네이션: 응답 헤더 `tr_cont == "M"`이면 다음 페이지 존재

## Known Limitations
- Sector guardrail inactive (pykrx has no sector API)
- VKOSPI fetch disabled (KRX blocks AWS IPs)

## v86 VolumeBreakoutFeed Paper Ops
LLM-free strategy: pykrx 일봉 거래량 폭증 + 20일 고점 돌파. 활성화 2026-05-11 (PID 503343).

### 운영 파라미터 (.env)
- `VOLUME_BREAKOUT_FEED_ENABLED=true`
- `VOLUME_BREAKOUT_FEED_TICKERS=` 40 종목 (`scripts/v86_backtest.py::DEFAULT_UNIVERSE` 동일)
- `VOLUME_BREAKOUT_FEED_MIN_VOL_RATIO=2.0` — **운영 채택값 (보수적)**
  - 백테스트 권고는 `3.0` (avg_net=+2.16%, trades=30/180일)
  - 운영 채택 `2.0` (avg_net=+1.30%, trades=68/180일) — 1주일 검증 후 3.0 승격 검토
- `VOLUME_BREAKOUT_FEED_LOOKBACK_N=20`, `POLL_INTERVAL_S=3600`, `SIGNAL_COOLDOWN_S=86400`

### 진단 / 모니터링
```bash
# 1) Universe ground truth — 현재 시점 어떤 종목이 시그널 자격을 충족하는지
.venv/bin/python scripts/v86_diagnose.py

# 2) 30분 paper 모니터링 watcher (1분 granularity, 종료 시 wrap.md 자동 생성)
.venv/bin/python scripts/v86_paper_watch.py \
  --log logs/paper-v86-<DATE>.log --baseline-trades <N>

# 3) 시그널 SQL (trade_history.db)
.venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/trade_history.db'); \
  print(c.execute(\"SELECT COUNT(*) FROM trades WHERE decision_source='volume_breakout' \
  OR decision_reason LIKE '%v86 breakout%'\").fetchone()[0])"
```

### Daemon 재가동 (실거래 시 SIGTERM grace 필수)
```bash
# Paper 모드 (3s grace OK):
kill "$PID"; sleep 3; kill -9 "$PID" 2>/dev/null
nohup setsid .venv/bin/python -m kindshot --paper > logs/paper-v86-<DATE>.log 2>&1 < /dev/null & disown

# 실거래 모드 (architect 권고: SIGTERM 30s):
kill -TERM "$PID"; sleep 30; kill -KILL "$PID" 2>/dev/null
```

### 알려진 cleanup 항목
- `091990` (셀트리온헬스케어): 2024 합병 상폐 → universe 교체 필요 (`fire-w12-ks-v86-universe-cleanup`)
- `cooldown_s=86400` vs backtest hold=5d 비대칭 (`fire-w12-ks-v86-cooldown-design`)
