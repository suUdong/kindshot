# Phase 1 백테스트: kindshot 시그널 수익성 검증

생성일: 2026-03-29 02:55

## 요약

- 전체 BUY 시그널: 87건
- v78 가드레일 완화 통과: 42건 (중복 제거 후)
- v78 가드레일 차단: 32건
- 분석 기간: 20260318 ~ 20260327

### v78 가드레일 완화 기준

| 항목 | 기존 | v78 완화 |
|------|------|----------|
| min_buy_confidence | 78 | 73 |
| chase_buy_pct | 3.0% | 5.0% |
| no_buy_after | 15:00 | 15:15 |
| fast_profile_no_buy_after | 14:00 | 14:30 |
| min_intraday_value_vs_adv20d | 0.15 | 0.05 |
| orderbook_liquidity | 100% | 50% |

### 차단 사유 분포

| 사유 | 건수 |
|------|------|
| MARKET_CLOSE_CUTOFF | 19 |
| LOW_CONFIDENCE | 7 |
| CHASE_BUY_BLOCKED | 6 |

## 수익률 분석

| 지표 | T+1 | T+5 | T+30 |
|------|-----|-----|------|
| 분석 건수 | 24 | 23 | 0 |
| 승률 | 12.5% | 47.8% | N/A% |
| 평균수익률 | -4.25% | 2.39% | N/A% |
| 중간값 | -5.34% | -2.16% | N/A% |
| 최대 | 5.08% | 38.54% | N/A% |
| 최소 | -10.91% | -8.93% | N/A% |

## Paper → Live 전환 판정

기준: 승률 50% 이상 + 평균수익률 양수

| Horizon | 판정 |
|---------|------|
| T+1 | **FAIL** ❌ |
| T+5 | **FAIL** ❌ |
| T+30 | 데이터 부족 |

### 종합 판정

> **NOT READY** — 기준 미충족, 추가 최적화 필요

## 시그널별 상세

| 날짜 | 종목 | 버킷 | conf | 진입가 | T+1(%) | T+5(%) | T+30(%) | 원래가드레일 |
|------|------|------|------|--------|--------|--------|---------|-------------|
| 20260319 | 474610 | POS_STRONG | 82 | 10,000 | -5.5 | 6.5 | N/A | INTRADAY_VALUE_TOO_THIN |
| 20260319 | 042660 | POS_WEAK | 75 | 130,000 | -0.69 | -3.31 | N/A | INTRADAY_VALUE_TOO_THIN |
| 20260319 | 035420 | POS_STRONG | 78 | 220,500 | 0.45 | -4.08 | N/A | INTRADAY_VALUE_TOO_THIN |
| 20260319 | 001040 | POS_STRONG | 82 | 197,000 | 5.08 | 3.05 | N/A | INTRADAY_VALUE_TOO_THIN |
| 20260319 | 005380 | POS_STRONG | 78 | 522,000 | -0.96 | -6.13 | N/A | PASSED |
| 20260319 | 000660 | POS_STRONG | 82 | 1,013,000 | -0.59 | -7.9 | N/A | PASSED |
| 20260320 | 034730 | POS_STRONG | 76 | 360,000 | -8.61 | -7.22 | N/A | INTRADAY_VALUE_TOO_THIN |
| 20260320 | 041020 | POS_STRONG | 76 | 5,040 | -7.54 | -7.84 | N/A | INTRADAY_VALUE_TOO_THIN |
| 20260320 | 017670 | POS_STRONG | 76 | 78,800 | -5.84 | 1.4 | N/A | INTRADAY_VALUE_TOO_THIN |
| 20260320 | 032350 | POS_STRONG | 78 | 21,400 | -7.71 | -7.24 | N/A | INTRADAY_VALUE_TOO_THIN |
| 20260320 | 000250 | POS_STRONG | 82 | 907,000 | 3.75 | 22.49 | N/A | INTRADAY_VALUE_TOO_THIN |
| 20260320 | 267260 | POS_STRONG | 82 | 959,000 | -4.8 | -4.59 | N/A | INTRADAY_VALUE_TOO_THIN |
| 20260320 | 042660 | POS_WEAK | 75 | 129,100 | -7.9 | -4.57 | N/A | INTRADAY_VALUE_TOO_THIN |
| 20260320 | 068270 | POS_STRONG | 88 | 202,000 | -6.78 | 1.98 | N/A | PASSED |
| 20260320 | 010140 | POS_STRONG | 82 | 28,550 | -8.93 | -8.93 | N/A | INTRADAY_VALUE_TOO_THIN |
| 20260320 | 358570 | POS_STRONG | 82 | 14,570 | -10.91 | 0.75 | N/A | ORDERBOOK_TOP_LEVEL_LIQUIDITY |
| 20260320 | 066970 | POS_WEAK | 76 | 112,100 | 0.0 | 38.54 | N/A | ORDERBOOK_TOP_LEVEL_LIQUIDITY |
| 20260320 | 298040 | POS_STRONG | 82 | 2,735,000 | -5.34 | -2.16 | N/A | PASSED |
| 20260320 | 259960 | POS_STRONG | 76 | 235,500 | -3.18 | 8.07 | N/A | PASSED |
| 20260320 | 373220 | POS_STRONG | 78 | 375,500 | -5.19 | 5.06 | N/A | PASSED |
| 20260320 | 016360 | POS_STRONG | 82 | 102,300 | -7.92 | -4.59 | N/A | PASSED |
| 20260320 | 237690 | POS_STRONG | 82 | 150,800 | -5.5 | 5.64 | N/A | ORDERBOOK_TOP_LEVEL_LIQUIDITY |
| 20260320 | 007810 | POS_STRONG | 75 | 64,600 | -5.88 | 30.03 | N/A | MARKET_CLOSE_CUTOFF |
| 20260323 | 439260 | POS_STRONG | 82 | 88,800 | -1.46 | N/A | N/A | INTRADAY_VALUE_TOO_THIN |
| 20260327 | 456010 | POS_STRONG | 73 | 15,350 | N/A | N/A | N/A | LOW_CONFIDENCE |
| 20260327 | 002990 | POS_STRONG | 78 | 5,050 | N/A | N/A | N/A | PASSED |
| 20260327 | 070300 | POS_STRONG | 80 | 1,853 | N/A | N/A | N/A | PASSED |
| 20260327 | 001680 | POS_STRONG | 78 | 21,000 | N/A | N/A | N/A | PASSED |
| 20260327 | 068270 | POS_STRONG | 78 | 206,000 | N/A | N/A | N/A | PASSED |
| 20260327 | 298380 | POS_STRONG | 82 | 180,100 | N/A | N/A | N/A | CHASE_BUY_BLOCKED |
| 20260327 | 068760 | POS_STRONG | 78 | 58,500 | N/A | N/A | N/A | OPENING_LOW_CONFIDENCE |
| 20260327 | 001260 | POS_STRONG | 78 | 8,840 | N/A | N/A | N/A | OPENING_LOW_CONFIDENCE |
| 20260327 | 071970 | POS_STRONG | 78 | 76,700 | N/A | N/A | N/A | ORDERBOOK_TOP_LEVEL_LIQUIDITY |
| 20260327 | 192650 | POS_STRONG | 76 | 6,840 | N/A | N/A | N/A | LOW_CONFIDENCE |
| 20260327 | 272210 | POS_STRONG | 74 | 124,100 | N/A | N/A | N/A | LOW_CONFIDENCE |
| 20260327 | 006280 | POS_STRONG | 86 | 151,000 | N/A | N/A | N/A | PASSED |
| 20260327 | 054220 | POS_STRONG | 73 | 565 | N/A | N/A | N/A | LOW_CONFIDENCE |
| 20260327 | 034230 | POS_STRONG | 80 | 17,830 | N/A | N/A | N/A | CHASE_BUY_BLOCKED |
| 20260327 | 439260 | POS_STRONG | 75 | 85,700 | N/A | N/A | N/A | FAST_PROFILE_LATE_ENTRY |
| 20260327 | 112040 | POS_STRONG | 75 | 21,750 | N/A | N/A | N/A | LOW_CONFIDENCE |
| 20260327 | 013120 | POS_STRONG | 80 | 3,045 | N/A | N/A | N/A | FAST_PROFILE_LATE_ENTRY |
| 20260327 | 009830 | POS_STRONG | 76 | 35,650 | N/A | N/A | N/A | LOW_CONFIDENCE |
