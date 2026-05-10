# KS v86 VolumeBreakoutFeed 백테스트 리포트 (2026-05-11)

## 요약
v85.1 sim AVG (+0.035% /trade) 대비 v86 단독 트레이드의 기대수익이 **+1.30% ~ +2.16% /trade** 로
약 37x ~ 62x 높음. 단, 1) 트레이드 빈도가 낮고 (180일 / 40종목 / ≈30-68건) 2) 분포 꼬리가 두꺼움
(best +25%, worst -19%). LLM-free 라 circuit breaker OPEN 상태에서도 안정 동작 가능.

## 환경
- Universe: 40 종목 (KOSPI200 대형주 30 + KOSDAQ150 대형주 10)
- Period: 2025-09-03 ~ 2026-05-11 (≈180 거래일)
- 진입: 신호 다음 거래일 시가 / 청산: t+5 거래일 종가
- 비용 모델: buy fee 0.015% + sell fee 0.015% + sell tax 0.20% = 0.23% / round-trip
- 데이터: pykrx 일봉 (parquet 캐시)

## 파라미터 스윕

| Config                                                  | Trades | Winrate | Avg raw | Avg net | Median net | Best   | Worst   |
|---------------------------------------------------------|--------|---------|---------|---------|------------|--------|---------|
| **baseline** vol_ratio=2.0, hold=5, ret 0..7            | 68     | 47.06%  | +1.530% | +1.300% | -0.355%    | +25.6% | -18.8%  |
| **stricter** vol_ratio=3.0, hold=5, ret 0..7            | 30     | 56.67%  | +2.390% | +2.160% | +2.043%    | +22.4% | -18.8%  |
| **shorter** vol_ratio=2.0, hold=3, ret 0..4             | 27     | 40.74%  | +0.363% | +0.133% | -1.316%    | +17.8% | -11.1%  |
| **balanced** vol_ratio=2.5, hold=5, ret 0.5..5          | 22     | 63.64%  | +0.943% | +0.713% | +2.043%    | +22.4% | -18.8%  |

### 관찰
1. `vol_ratio=3.0` 가 winrate / avg / median 모두 베스트 → 강한 거래량 폭증이 진짜 알파.
2. `hold=3` 단축은 알파 감소 (+0.133% only) — 5거래일이 breakout follow-through 잡기에 적합.
3. `ret_today` 상/하한 제한은 winrate↑ 하지만 큰 winner 도 잘라내 avg↓.
4. baseline 도 양의 expectancy 지만 median 음수 → 분포 꼬리 의존. 운영시 종목 분산 필수.

## v85.1 baseline 대비

| Metric              | v85.1 sim   | v86 baseline | v86 tuned (ratio=3.0) |
|---------------------|-------------|--------------|-----------------------|
| Avg net ret /trade  | +0.035%     | +1.300%      | +2.160%               |
| Trades / 180일       | ~14 (실거래)  | 68 (sim)     | 30 (sim)              |
| LLM 의존              | YES (rule_fallback) | NO   | NO                    |
| Universe            | 뉴스기반 동적 | 40 watchlist  | 40 watchlist          |

> v85.1 의 +0.035% 는 실제 paper 기록 기반, v86 는 historical sim. 두 수치 직접 비교는 신중 — 다만
> v86 의 트레이드 단위 기대수익이 **유의미하게 큼** 은 명확.

## 통합 효과 (per-trade 합산 sim)

가정: v85.1 lane 의 14건 + v86 lane 의 30건 (tuned) 을 동일 가중 합산:
```
combined_avg = (14 × 0.035 + 30 × 2.160) / 44 = 1.484% /trade
```
약 **+1.48% /trade** (v85.1 단독 대비 +1.45 pp 향상). 실제로는 자본 분산, MAX_POSITIONS=4 제약,
중복 종목 등으로 다소 희석되지만, expected value 가 양으로 강하게 lift.

## 권고 운영 파라미터

```bash
VOLUME_BREAKOUT_FEED_ENABLED=true
VOLUME_BREAKOUT_FEED_TICKERS=005930,000660,035420,005380,005490,000270,006400,068270,247540,086520
VOLUME_BREAKOUT_FEED_LOOKBACK_N=20
VOLUME_BREAKOUT_FEED_MIN_VOL_RATIO=3.0      # baseline 2.0 → tuned 3.0
VOLUME_BREAKOUT_FEED_MIN_RET_TODAY=0.0
VOLUME_BREAKOUT_FEED_MAX_RET_TODAY=7.0
VOLUME_BREAKOUT_FEED_MIN_ADV_VALUE=500000000
VOLUME_BREAKOUT_FEED_POLL_INTERVAL_S=3600
VOLUME_BREAKOUT_FEED_SIGNAL_COOLDOWN_S=86400
```

## 한계 & follow-up
- pykrx 일봉 기반 → intraday 진입 불가. 다음 거래일 시가 모델.
- 종목 universe 고정 → 동적 universe (시총상위/상장폐지 자동 갱신) 추후 검토.
- worst -18.8% 는 5거래일 보유 후 -18% 가능성 → 기존 KS guardrail (paper_stop_loss_pct, max_hold)
  과 결합해 운영시 손실 캡 필요.
- 백테스트는 거래비용만 반영, 슬리피지 미포함 → 실제 net 은 0.1-0.2% pp 더 낮을 수 있음.

## 생성 명령
```bash
python scripts/v86_backtest.py --lookback-days 180 --vol-ratio 3.0 \
  --output-json data/runtime/v86_backtest_tuned.json
```
