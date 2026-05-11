# KS v86 Paper 활성화 — 진단 리포트 (2026-05-11 09:01 KST)

## TL;DR
- v86 paper daemon **활성화 성공** (PID 503343, `Strategy started: volume_breakout`)
- **첫 30분 윈도우 (08:50 ~ 09:27 KST) v86 시그널 0건** — pykrx + ratio=2.0 + 시장 regime
  조합으로 **예측 가능한 결과**
- v86 polling=3600s → 다음 scan **09:50 KST**. 현재 universe ground truth 0/40 qualifies →
  09:50 scan 도 시그널 0건 가능성 높음
- Universe cleanup 필요 (091990 셀트리온헬스케어 → 2024 합병 상폐, no_data)
- `ratio=3.0` 승격 보류 — 현 시장 regime 에서는 ratio=2.0 도 시그널 불발 중

## v86 universe ground truth (09:01 KST, `scripts/v86_diagnose.py`)

| Reject bucket          | Count | 의미 |
|------------------------|-------|------|
| no_breakout            | 36    | close_today ≤ prior_high_20d |
| vol_ratio_low          | 3     | vol_today / prior_avg_vol_20d < 2.0 (실제 0.02~0.08x) |
| no_data                | 1     | 091990 셀트리온헬스케어 — 합병 상폐 |
| **qualifies**          | **0** | **시그널 발생 가능 종목 없음** |

CSV: `data/runtime/v86_diagnose_20260511_090158.csv`

### no_breakout 분포 샘플 (close_today vs prior_high_20d)

| Ticker | Close | Prior 20d High | Gap |
|--------|-------|----------------|-----|
| 010130 (고려아연)    | 1,575,000 | 1,786,000 | -11.8% |
| 196170 (알테오젠)    |   335,000 |   389,500 | -14.0% |
| 207940 (삼성바이오)  | 1,471,000 | 1,625,000 |  -9.5% |
| 005380 (현대차)     |   624,000 |   647,000 |  -3.6% |
| 067310 (하나마이크론) |    44,150 |    44,850 |  -1.6% |

→ **시장 전반 약세 / range-bound** — 대형주 universe 자체가 20일 고점 아래.

### vol_ratio_low 종목 (09:01 KST 시점, 장 시작 1분 후 데이터)

| Ticker | vol_today / prior_avg | 추정 원인 |
|--------|------------------------|-----------|
| 005930 (삼성전자) | 0.08x | pykrx today bar 미반영 / 장초반 |
| 000660 (SK하이닉스) | 0.06x | 동일 |
| 032830 (삼성생명)   | 0.02x | 동일 |

→ pykrx 일봉이 장중에는 partial 누적. 09:50 daemon scan 시점에는 정상화 예상.

## 활성화 검증 체크리스트

| 항목 | 상태 | 근거 |
|------|------|------|
| daemon PID 교체 | ✅ | 324287 → 503343 (PPID=systemd-user, setsid 분리) |
| .env 적용 | ✅ | 40 tickers, ratio=2.0, lookback=20 |
| Strategy registry | ✅ | `Strategy registered: volume_breakout (source=TECHNICAL, enabled=True)` |
| Feed start | ✅ | `VolumeBreakoutFeed starting (tickers=40)` |
| active strategies | ✅ | `['alpha_feed', 'news', 'volume_breakout']` |
| 첫 scan (08:50 KST) | ✅ | scan 실행, signals=0 (정상 — pre-market) |
| 시그널 → 트레이드 | ⏳ | universe 0/40 → 09:50 scan 도 0 예상 |
| trade_history 변화 | ⏳ | baseline=14, 30분 윈도우 watcher CSV 추적 중 |

## 30분 watcher (08:57 ~ 09:27 KST) 기대값

베이스라인 trades_count=14. v86 lane 자체로는 09:50 까지 signal 없을 예정 → delta 변동은
**v85.1 lane (NEWS strategy)** 에서만 발생 가능. 단 Anthropic credit OPEN 상태로 LLM lane
fallback 비율 높음.

## 왜 0/40 인가 — backtest 와 정합성

180일 백테스트 (`docs/2026-05-11-ks-v86-backtest-report.md`):
- ratio=2.0: 68 trades / 40 tickers / 180일 ≈ **0.0094 trades/ticker/day** = 0.38/day (universe-wide)
- ratio=3.0: 30 trades / 40 tickers / 180일 ≈ **0.0042 trades/ticker/day** = 0.17/day

→ **하루 평균 0.4건도 안 되는 빈도**. 09:01 시점 0/40 은 통계적으로 **정상 범위**.
딱 30분 윈도우 안에 시그널 발생 확률은 < 5%.

추가로, 백테스트 기간 (2025-09 ~ 2026-05) 동안의 시장 regime 가 다양했지만 평균 0.38/day.
오늘이 단순히 "low day" 일 가능성 높음.

## 즉시 조치 권고

1. **091990 (셀트리온헬스케어) universe 제거** — 합병 상폐된 종목.
   교체: `091990` → `196170` 이미 있음, 추가 후보 `383220` (F&F, KOSDAQ 대형주) 등.
   → 별도 PR (코드 변경, 백테스트 재실행 필요) — 본 fire 범위 밖.

2. **ratio=3.0 승격 보류** — ratio=2.0 도 0/40 인 상태에서 ratio=3.0 승격은 시그널 빈도 더 떨어뜨림.
   1주일 paper 운영 결과 (실제 발생 건수, winrate, avg_net) 후 재평가.

3. **Monitor + watcher 자연 종료 대기** — Monitor (09:27 timeout), watcher (09:27 마지막 iter).
   강제 종료 불필요.

4. **다음 daemon v86 scan 09:50 KST 결과 확인** — 09:55 부근 logs/paper-v86-20260511.log 에서
   `v86 breakout` 또는 `VolumeBreakoutFeed` 관련 라인 grep.

## 사용자 escalation (재정리)

| # | 항목 | 우선순위 | 책임 |
|---|------|---------|------|
| 1 | Anthropic credit 충전 | High | 사용자 |
| 2 | DART_API_KEY 설정 | Med | 사용자 |
| 3 | alpha-scanner :8765 가동 | Med | fire-w11-alpha-scanner |
| 4 | y2i ip_block 해제 | Med | fire-w11-y2i-twitter |
| 5 | KIS_REAL key (실거래용) | Low (paper 모드 OK) | 사용자 |
| 6 | universe 091990 제거 | Low | 다음 fire |
| 7 | ratio=3.0 승격 결정 | Low (1주일 후) | 사용자 |

## 관련 commits (이번 세션)
- `b5287bf` docs: KS v86 paper daemon 활성화 (PID 503343, ratio=2.0, tickers=40)
- `15550d2` feat: v86 paper 활성화 검증 워처
- `285f194` chore: v86 paper watcher 가동 절차 docs + .v86_watch.pid gitignore
- `f0a995b` feat: v86 universe ground-truth 진단 스크립트
- `(this commit)` docs: KS v86 진단 리포트 — 0/40 qualifies, 시장 regime 진단
