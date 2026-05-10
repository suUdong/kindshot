# DART PEAD Phase 2 (LLM Tone Scoring) — 통합 결정

**Date:** 2026-05-11
**Status:** DEFER (deferred to a later phase)
**Reference spec:** /home/wdsr88/workspace/research/reports/fire-w11-topic1-pead-spec.md

## Phase 1 현황 (dcf59e0, 2026-03-29)

- 코드: 활성
  - `config.dart_earnings_enabled = True` (default)
  - `src/kindshot/dart_earnings_strategy.py` 구현 완료
  - `src/kindshot/main.py:135-152` wire 정상 (earnings_queue + DartEnricher + DartEarningsStrategy)
  - `tests/test_dart_earnings_strategy.py` 287 lines test coverage
- 운영: dormant
  - `DART_API_KEY` 미설정 → 실제 라이브 데이터 수집 안 됨
  - `data/trade_history.db` 14건의 `decision_source` 모두 빈 값 → PEAD 시그널 거래 흔적 0건

## Phase 2 (Gemini spec) 핵심

- LLM tone scoring: 공시 본문/가이던스의 sentiment 정량화
- Structured JSON output: sentiment.score, guidance.confidence_level, overall_alpha_score
- 시그널 합성: Type A (Surprise + Bullish), Type B (Sentiment-Led), Type C (Bull Trap)
- 피드 가중치: `Final Score = w1*PEAD + w2*News + w3*Y2i` + earnings season dynamic weighting

## 본 세션 결정: 통합 DEFER

이번 세션 목표는 KS 기존 14건 trade 손실 구조 개선 (AVG -0.646% → ≥ 0%).
PEAD Phase 2는 다음 이유로 별도 phase로 분리:

1. **현재 손실의 직접 원인이 아님**: 14건 모두 NEWS/일반 시그널, PEAD trade 0건
2. **운영 prerequisite 미충족**: DART_API_KEY 설정 + 라이브 실적 공시 수집 안정화 선행
3. **추가 LLM 비용**: 본문 분석 LLM 호출은 latency + 토큰 비용 증가, ROI 검증 필요
4. **scope creep 위험**: 본 세션의 4 core lever (exit-logic, 09시 차단, conf floor, sizing) 완수 우선

## 다음 phase 진입 조건

- [ ] DART_API_KEY 운영 환경에 설정
- [ ] DART RSS 폴링 ≥ 1주 안정 가동 (live earnings 공시 수집 검증)
- [ ] Phase 1 PEAD trade ≥ 5건 누적 (실적 + exit_ret 표본 확보)
- [ ] Phase 2 LLM 프롬프트 backtest cost-benefit 계산
- [ ] Anthropic API 크레딧 잔여 ≥ $20 (Haiku 사용 가정)

위 5개 조건 충족 시 별도 phase에서 LLM tone scoring 통합.
