# Strategy Performance Analysis
Generated: 2026-03-26 06:02

## Summary
- Total events: 4938
- Trades executed: 23
- Signals skipped: 4915
- Profitable: 0
- Losing: 0
- Avg return (close): 0.00%

## Bucket Distribution
- UNKNOWN: 2639
- IGNORE: 1173
- POS_STRONG: 502
- POS_WEAK: 349
- NEG_STRONG: 241
- NEG_WEAK: 34

## Top Skip Reasons
- UNKNOWN_BUCKET: 1844
- IGNORE_BUCKET: 1173
- CORRECTION_EVENT: 542
- POS_WEAK_BUCKET: 335
- DUPLICATE: 253
- ADV_TOO_LOW: 247
- NEG_BUCKET: 241
- LLM_PARSE: 75
- SPREAD_TOO_WIDE: 48
- NEG_WEAK_BUCKET: 34

## Potential Missed Opportunities (POS skipped by QUANT/GUARDRAIL)
- 002210 (ADV_TOO_LOW): [마켓인]동성제약 회생계획안 결국 부결…태광 인수 가도 ‘빨간불’
- 474610 (INTRADAY_VALUE_TOO_THIN): RF시스템즈, 글로벌 방산 수요 확대 속 '구조적 성장'…대규모 수주로 턴어라운드 '본격화'
- 474610 (INTRADAY_VALUE_TOO_THIN): [클릭 e종목]"RF시스템즈, 167억 대형 수주…실적 가시성 확보"
- 204840 (ADV_TOO_LOW): '개량신약' 지엘팜텍, "작년 역대 최대 매출, 흑자 전환 일궈"
- 474610 (INTRADAY_VALUE_TOO_THIN): RF시스템즈, 대형 수주에 실적 가시성↑…사업 체질 전환 본격화-하나

## Insights
- Win rate: 0% (0/23)
- Avg return at close: 0.00%
- UNKNOWN bucket is 53% of events. Bucket keywords need expansion.
- 1 LLM errors in replay reports. API retry logic or context card robustness needs work.