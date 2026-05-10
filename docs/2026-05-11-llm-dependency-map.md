# KS LLM 의존성 매핑 (2026-05-11)

## 배경
Anthropic API credit 부족으로 LLM circuit breaker가 OPEN 될 수 있는 상황.
이 문서는 각 전략이 LLM 호출에 의존하는지, 그리고 rule_fallback 만으로 동작 가능한지 정리한다.

## 매핑 표

| Strategy / Component       | LLM 호출 | Rule fallback | Circuit OPEN 시 동작 | 비고 |
|---------------------------|----------|---------------|---------------------|------|
| NewsStrategy              | YES (via `decision.py` LlmClient) | YES (`decision.py:598-645`) | rule_fallback 동작 — HIGH_CONVICTION 키워드 + POS 버킷만 BUY | 진입률 보수적으로 떨어짐 |
| TechnicalStrategy         | NO       | n/a           | 정상 동작 (pykrx + KIS price) | watchlist 종목만 |
| RebalanceFeed (Topic 2)   | NO       | n/a           | 정상 동작 (6/12월 윈도우만) | 현재 candidates 비어 있음 |
| AlphaFeed                 | NO       | n/a           | 정상 동작 (외부 alpha-scanner 의존) | 8765 포트 가동 필요 |
| Y2iFeed                   | NO       | n/a           | 정상 동작 (외부 y2i 의존) | kindshot_feed.json stale 시 무신호 |
| DartBuybackStrategy       | NO       | n/a           | 정상 동작 (DART API 의존) | DART_API_KEY 필요 |
| DartEarningsStrategy      | NO       | n/a           | 정상 동작 (DART API 의존) | DART_API_KEY 필요 |
| ShortOverheatingStrategy  | NO       | n/a           | 정상 동작 (KRX 공매도 데이터) | KRX AWS IP 블록 가능성 |
| UnknownReviewEngine       | YES      | NO            | OFF — UNKNOWN 자동 승급 일시 중단 | 검토 큐만 적재됨 |

## Circuit breaker OPEN 상태에서 가동 가능한 전략

쉘드 동작 가능 (LLM 무관):
- TechnicalStrategy
- RebalanceFeed
- AlphaFeed (alpha-scanner 살아 있을 때)
- Y2iFeed (y2i scheduler 살아 있을 때)
- DartBuybackStrategy, DartEarningsStrategy
- ShortOverheatingStrategy

부분 동작 (rule_fallback):
- NewsStrategy — decision.py 내 rule_fallback 경로 (HIGH_CONVICTION 키워드 + POS 버킷)

차단:
- UnknownReviewEngine 자동 승급

## 시사점

현재 paper daemon 운영 중인 KS 환경 (circuit OPEN 가능성 ↑) 에서는 LLM-independent 전략 비중을
높여야 한다. v86 = VolumeBreakoutFeed 는 이 매핑에서 가장 큰 공백 (TechnicalStrategy 가 watchlist
제한적이라 신호 빈도 낮음) 을 메우기 위한 추가 폴링 전략으로 도입한다.
