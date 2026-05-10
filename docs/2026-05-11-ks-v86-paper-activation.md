# KS v86 VolumeBreakoutFeed Paper 활성화 (2026-05-11)

## 요약
v86 백테스트 결과 (sim AVG +1.484% /trade, baseline -0.646% 대비 +2.13 pp delta) 검증 완료 후
실제 paper daemon 에 v86 lane 활성화. 보수적 파라미터 (`vol_ratio=2.0`) 로 시작.

## 활성화 시각
- **2026-05-11 08:50 KST** (장 개장 전, 09:00 KST 개장)
- 기존 daemon **PID 324287** (v85.1, 08:26 시작) → **신규 PID 503343** (v85.1+v86)
- 신규 daemon PPID=528 (systemd --user) — `setsid` 분리 정상

## 변경 파라미터 (.env)
백업: `.env.bak.20260511084827` (직전 v85.1 .env)

```bash
# v86 VolumeBreakoutFeed (added 2026-05-11, conservative ratio=2.0)
VOLUME_BREAKOUT_FEED_ENABLED=true
VOLUME_BREAKOUT_FEED_TICKERS=005930,000660,035420,035720,005380,051910,006400,207940,068270,005490,000270,005935,066570,003670,012330,028260,017670,030200,015760,055550,105560,086790,316140,032830,009150,036460,010130,010950,096770,034730,247540,086520,091990,196170,112040,041510,035900,067310,058470,293490
VOLUME_BREAKOUT_FEED_LOOKBACK_N=20
VOLUME_BREAKOUT_FEED_MIN_VOL_RATIO=2.0
VOLUME_BREAKOUT_FEED_MIN_RET_TODAY=0.0
VOLUME_BREAKOUT_FEED_MAX_RET_TODAY=7.0
VOLUME_BREAKOUT_FEED_MIN_ADV_VALUE=500000000
VOLUME_BREAKOUT_FEED_POLL_INTERVAL_S=3600
VOLUME_BREAKOUT_FEED_SIGNAL_COOLDOWN_S=86400
```

- Universe = `scripts/v86_backtest.py::DEFAULT_UNIVERSE` 동일 (KOSPI 30 + KOSDAQ 10)
- `vol_ratio=2.0` 보수적 — 백테스트 baseline (trades=68, winrate=47.06%, avg_net=+1.300%)
- `vol_ratio=3.0` (sim avg_net=+2.160%, trades=30) 은 paper 검증 후 사용자 승인 필요

## Config 검증
```
$ .venv/bin/python -c "from kindshot.config import Config; c = Config(); ..."
vb_enabled= True
vb_tickers_count= 40
vb_min_vol_ratio= 2.0
vb_min_adv_value= 500000000.0
vb_poll_interval_s= 3600.0
alpha_feed_enabled= True       # 잔존 (단, 8765 미가동 → fetch fail)
y2i_feed_enabled= True         # 잔존 (단, ip_block stale)
```

## Restart 절차 (실제 실행)
```bash
cd /home/wdsr88/workspace/kindshot
OLD_PID=$(cat .ks-paper.pid)              # 324287
kill "$OLD_PID"; sleep 3
# graceful 미완 → SIGKILL fallback
kill -9 "$OLD_PID"
nohup setsid .venv/bin/python -m kindshot --paper \
  > logs/paper-v86-20260511.log 2>&1 < /dev/null & disown
echo $! > .ks-paper.pid                   # 503343
```

## 검증 (08:51 KST 시점)
- [x] config: `volume_breakout_feed_enabled=True, tickers_count=40`
- [x] 신규 PID 503343 alive (PPID=528 systemd-user, setsid 분리 정상)
- [x] 로그 `logs/paper-v86-20260511.log` 생성 (5KB+)
- [x] `Strategy registered: volume_breakout (source=TECHNICAL, enabled=True)`
- [x] `VolumeBreakoutFeed registered (enabled=True, tickers=40, lookback=20, min_vol_ratio=2.00x)`
- [x] `Strategy started: volume_breakout`
- [x] active strategies: `['alpha_feed', 'news', 'volume_breakout']`
- [ ] 30min 후 `trade_history.db` rowcount 변화 (베이스라인 trades=14)
- [ ] v86 첫 시그널 발생 (strategy_name=`volume_breakout`)

## 모니터링 명령
```bash
# 로그 follow
tail -f /home/wdsr88/workspace/kindshot/logs/paper-v86-20260511.log

# trade_history rowcount
.venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/trade_history.db'); \
  print(c.execute('SELECT COUNT(*) FROM trades').fetchone()[0])"

# v86 시그널만 추출 (strategy_name 컬럼 존재 가정)
.venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/trade_history.db'); \
  rows=c.execute(\"SELECT * FROM trades WHERE reason LIKE '%v86%' OR reason LIKE '%volume_breakout%'\").fetchall(); \
  print(len(rows), rows[:3])"
```

## 사용자 Escalation 항목
1. **`vol_ratio=2.0` → `3.0` 승격**: paper 첫 주 결과 (trades, winrate, avg_net) 확인 후 결정
2. **DART API key 갱신**: `DART_API_KEY` 미설정 → DartBuybackStrategy / DartEarningsStrategy
   `inactive_but_requested` 상태 (PEAD Phase 1 비활성)
3. **alpha-scanner (port 8765) 수동 가동**: AlphaFeed `Cannot connect 127.0.0.1:8765` 반복 —
   `fire-w11-alpha-scanner` 책임
4. **y2i ip_block_state.json clear**: `fire-w11-y2i-twitter` 책임. Y2iFeed enabled 이지만
   신호 0건 (block stale)
5. **Anthropic API credit 충전**: circuit breaker OPEN — v85.1 LLM lane 무력화. v86 는 영향 없음
   (LLM-free) 이지만 unknown_review fallback 부재
6. **KIS_REAL key**: `WITHOUT real API keys — price snapshots will use VTS (stale prices)` 경고.
   실거래 진입 전 real-time KIS key 필요. 현 paper 모드에서는 OK

## 한계 및 주의
- v86 polling=3600s (1h) → 최초 시그널까지 최대 1h 대기. 시간대별 분포 불균형 가능
- 첫 시그널 발생 시 **conductor 에 즉시 보고** 필요
- worst-case backtest -18.8% 단일 트레이드 — KS guardrail (`paper_stop_loss_pct=-1.0`,
  `max_hold_minutes=30`) 와 결합되어 실제 손실은 크게 제한됨
- VTS price 모드 운영 중 — exit 가격 신뢰도 낮음. real-time KIS key 적용 후 PAPER PnL 신뢰도↑

## 30분 후 검증 자동화 (수동 실행)
```bash
# 09:21 KST 부근에 실행
cd /home/wdsr88/workspace/kindshot
.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('data/trade_history.db')
n = c.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
print(f'trades count = {n} (baseline=14, delta={n-14})')
v86 = c.execute(\"SELECT COUNT(*) FROM trades WHERE reason LIKE '%v86%' OR reason LIKE '%volume_breakout%'\").fetchone()[0]
print(f'v86 signals = {v86}')
"
grep -c "VolumeBreakoutFeed\|volume_breakout" logs/paper-v86-20260511.log
```
