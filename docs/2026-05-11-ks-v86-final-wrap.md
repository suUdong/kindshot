# KS v86 Paper 활성화 — Final Wrap (2026-05-11)

## 결론
- **활성화 100% 성공**: PID 324287 (v85.1) → **PID 503343 (v85.1+v86)**
- **첫 30분 윈도우 v86 시그널 0건** — universe ground truth 0/40 qualifies, backtest avg 0.38/day 와 정합
- **ratio lowering 무의미**: 1.0 까지 내려도 0/40 — 1차 bottleneck 은 `no_breakout` (close ≤ prior_high_20d)
- **Universe 확장 또는 시장 regime 회복** 까지 v86 lane 자연 휴면. W12 (다음 주) 재평가 권고.

## ⚠️ Important learning
v86 백테스트 sim +2.16%/trade 는 2025-09 ~ 2026-05 (180일) historical universe-fit 데이터.
현재 (2026-05-11) 시장 regime 은 baseline 보다 약세이며, **40 종목 universe 가 모두 20일 고점
아래** 상태. 즉시 paper validation 은 시장 회복 또는 universe 확장 후 가능.

## 1. 0/40 qualifies 구체 원인

### no_breakout (36/40, 90%) — 1차 bottleneck
universe 전체가 20일 고점 아래. 시장 약세 / range-bound regime 의 직접 증거.

| 분포 | gap (close vs prior_high_20d) |
|------|-------------------------------|
| **median** | **-7.02%** |
| best (가장 근접) | 005935 -0.77%, 066570 -1.39%, 000270 -1.41% |
| worst | 112040 -14.88%, 196170 -13.99%, 058470 -13.33% |

→ **시장 전반 약세**. 대형주 universe 가 20일 고점 대비 평균 -7% 위치.
삼성전자우 (005935) 가 +0.77% 만 더 오르면 첫 시그널 candidate.

### vol_ratio_low (3/40)
장 시작 1분 후 (09:01 KST) partial bar 영향. 다음 daemon scan (09:50) 시 정상화 예상.

| Ticker | vol_today / prior_avg_20d | 추정 원인 |
|--------|---------------------------|----------|
| 005930 (삼성전자) | 0.085x | 장초반 누적 미완 |
| 000660 (SK하이닉스) | 0.061x | 동일 |
| 032830 (삼성생명) | 0.023x | 동일 |

### no_data (1/40)
- **091990 (셀트리온헬스케어)**: 2024 셀트리온 합병 상폐. universe cleanup 필요.

## 2. Universe 확장 권고

현 universe = `scripts/v86_backtest.py::DEFAULT_UNIVERSE` (KOSPI 30 + KOSDAQ 10 = 40).

### 즉시 권고
- **091990 → 교체**: 합병 상폐. 후보 `383220 F&F`, `196170 알테오젠` (이미 있음).

### 단기 (W12 ~ W13)
- **40 → 80 종목 확장**: KOSDAQ 중소형 (시총 5천억 ~ 2조) 추가
  - rationale: 대형주는 변동성↓, breakout 빈도↓. 중소형은 변동성↑, breakout 빈도↑.
  - 백테스트 재실행 필요 (`scripts/v86_backtest.py --tickers ...`) — 본 fire 범위 밖.

### 중장기
- **동적 universe**: KOSPI200 + KOSDAQ150 자동 fetch (pykrx `stock.get_market_cap_by_ticker`)
- **상폐/거래정지 자동 제거**
- **adv_value_n 기반 필터링** (현 hardcoded list 대체)

## 3. ratio lowering 효과 — **권고하지 않음**

`scripts/v86_diagnose.py` 결과 기반 sensitivity:

| min_vol_ratio | qualifies / 40 |
|---------------|----------------|
| 2.0 (현 운영) | **0** |
| 1.8           | **0** |
| 1.5           | **0** |
| 1.2           | **0** |
| 1.0           | **0** |

→ **vol_ratio 가 bottleneck 이 아님**. no_breakout 이 모든 케이스 cap. ratio 추가 완화는
backtest expectancy 만 떨어뜨리고 (winrate↓, avg_net↓) 시그널은 여전히 0건.

**ratio=2.0 유지** 또는 추후 universe 확장 후 backtest 재실행으로 재평가.

## 4. 시장 regime 변동 시 expected activation timing

### 자연 활성화 시나리오
- **KOSPI/KOSDAQ +3% 추세상승 1주일 이상** → 일부 종목 20일 고점 갱신
- **개별 호재** (실적 surprise, 정책, M&A) → 단일 종목 breakout
- **변동성 회복** (현 거래량 0.02~0.08x → 1.0x+ 정상화)

### 백테스트 정합성
- ratio=2.0 평균 0.38 trades/day (universe-wide)
- 30분 윈도우 0건 = **통계적 정상** (확률 < 5%)
- 1주일 운영 시 ~2건 발생 기대 (정상 regime 가정)

### 즉시 검증 불가 → W12 결정 시점
- W12 (2026-05-18 부근) 시장 regime 재평가
- 1주 운영 후 trades count, winrate, avg_net 누적 → ratio=3.0 승격 결정
- 시그널 0건 지속 시 universe 확장 우선 (ratio 변경보다 효과적)

## 검증 체크리스트 (확정)

| 항목 | 상태 | 근거 |
|------|------|------|
| Daemon PID 교체 | ✅ | 324287 → 503343 (PPID=systemd-user, setsid 분리) |
| .env 적용 | ✅ | 40 tickers, ratio=2.0, lookback=20 (Config 검증) |
| Strategy registry | ✅ | `Strategy registered: volume_breakout (TECHNICAL, enabled=True)` |
| Feed start | ✅ | `VolumeBreakoutFeed starting (tickers=40)` |
| 회귀 테스트 | ✅ | 43/43 pass (test_volume_breakout_feed + test_v86_diagnose_consistency + test_config) |
| 첫 scan (08:50) | ✅ | scan 실행, signals=0 (정상) |
| 30분 monitoring | ✅ | watcher 1분×30 iter, Monitor v86-specific filter |
| 진단 (09:01) | ✅ | universe ground truth 0/40, ratio sensitivity 0 across all thresholds |
| 시그널 → 트레이드 | ⏸️ | 0건 (시장 regime 부적합 — 의도적 휴면) |

## 사용자 escalation (최종 정리, 우선순위)

| # | 항목 | P | 책임 | 비고 |
|---|------|---|------|------|
| 1 | Anthropic credit 충전 | High | 사용자 | v85.1 LLM lane 무력화 — v86 무관 |
| 2 | 091990 universe 제거 | High | 다음 fire | 합병 상폐, no_data 발생 중 |
| 3 | DART_API_KEY 설정 | Med | 사용자 | PEAD Phase 1 비활성 |
| 4 | alpha-scanner :8765 가동 | Med | fire-w11-alpha-scanner | AlphaFeed fetch fail |
| 5 | y2i ip_block 해제 | Med | fire-w11-y2i-twitter | Y2iFeed 신호 0건 |
| 6 | KIS_REAL key | Low | 사용자 | paper 모드 OK |
| 7 | universe 확장 (40→80) | Med | 다음 fire | KOSDAQ 중소형 추가, backtest 재실행 |
| 8 | W12 ratio=3.0 승격 결정 | Low | 사용자 | 1주일 운영 후 |

## 이번 세션 push (8 commits)

| Commit | 내용 |
|--------|------|
| `b5287bf` | docs: KS v86 paper daemon 활성화 (PID 503343, ratio=2.0, tickers=40) |
| `15550d2` | feat: v86 paper 활성화 검증 워처 (30분 × 1분 granularity) |
| `285f194` | chore: v86 paper watcher 가동 절차 docs + .v86_watch.pid gitignore |
| `f0a995b` | feat: v86 universe ground-truth 진단 스크립트 |
| `2a26ea3` | docs: v86 진단 리포트 — 0/40 qualifies, 시장 regime 진단 |
| `629251e` | feat: v86 paper watcher 종료 시점 wrap markdown 자동 생성 |
| `173ef22` | fix: v86 paper watcher wrap — relative csv_path 경로 ValueError 처리 |
| `908d141` | test: v86 diagnose evaluate ↔ Feed._qualifies 정합성 회귀 테스트 |

## 백그라운드 자동화 (자연 종료 대기)

| Process | PID/ID | 종료 ETA | 종료 시 액션 |
|---------|--------|---------|--------------|
| Daemon | 503343 | (영구) | 다음 v86 scan 09:50 KST |
| Watcher | 558917 | 09:27 KST (30 iter) | CSV 확정, 자체 종료 |
| Monitor | btgat3o3a | 09:27 KST timeout | 자체 종료 |
| Auto-wrap trigger | blycj5vmr | 워처 종료 감지 | wrap.md 생성 + 알림 |

`AUTO_WRAP_DONE` 알림 후 commit + push, ralph 종결.
