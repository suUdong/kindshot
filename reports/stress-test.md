# Stress Test Report — v82 월요일 실거래 준비

- **일시**: 2026-03-29 (토)
- **버전**: v82 (e1a09e9)
- **테스트 환경**: Python 3.12.3, pytest 9.0.2
- **전체 테스트**: 1130 passed, 1 skipped (25.82s)
- **스트레스 테스트**: 28/28 passed (3.74s)

---

## 1. Full-Day Simulation (9AM~3:30PM)

| 테스트 | 결과 | 설명 |
|--------|------|------|
| `test_time_windows_coverage` | PASS | 9개 장중 이벤트 → 5개+ POS 분류 확인 |
| `test_guardrail_state_resets_daily` | PASS | 일간 리셋: position_count, 연속손절, bought_tickers 모두 0 |
| `test_full_day_position_lifecycle` | PASS | BUY 3건 → SELL 1건 → BUY 1건 → 전량 매도: position_count 정확 추적 |

**결론**: 장중 전체 포지션 라이프사이클 정상 동작.

---

## 2. 동시 BUY 시그널 — max_positions 가드레일

| 테스트 | 결과 | 설명 |
|--------|------|------|
| `test_max_positions_blocks_5th_buy` | PASS | max_positions=4에서 5번째 BUY → `MAX_POSITIONS` 차단 |
| `test_max_positions_allows_after_sell` | PASS | 1건 매도 후 새 매수 허용 확인 |
| `test_concurrent_buy_signals_race` | PASS | 8건 동시 시그널 → 정확히 4건 수락, 4건 거부 |
| `test_sector_concentration_limit` | PASS | max_sector_positions=2 → 동일 섹터 3번째 매수 `SECTOR` 차단 |
| `test_consecutive_stop_loss_halt` | PASS | 3연속 손절 후 매수 차단 |

**결론**: 동시 다수 BUY 시그널 발생 시 max_positions(4), 섹터 집중(2), 연속손절(3) 가드레일 모두 정상 작동.

---

## 3. 뉴스 폭주 시나리오 (10건+ 동시)

| 테스트 | 결과 | 설명 |
|--------|------|------|
| `test_classify_12_headlines_under_1s` | PASS | 12건 분류 < 1초 (실제 수 ms) |
| `test_flood_deduplication` | PASS | 15건 중복 뉴스 → 5건 고유 이벤트로 축소 |
| `test_concurrent_llm_calls_semaphore` | PASS | 10건 동시 LLM 호출 → semaphore(4) 내에서 순차 처리, 전부 성공 |
| `test_flood_with_mixed_buckets` | PASS | POS/NEG/UNKNOWN 혼합 12건 → POS 4+, NEG 1+ 정확 분류 |

**결론**: 뉴스 폭주 시 분류 성능, 중복 제거, LLM 동시성 제어 모두 정상.

---

## 4. Circuit Breaker 트리거 시나리오

| 테스트 | 결과 | 설명 |
|--------|------|------|
| `test_anthropic_credit_exhaustion_opens_circuit` | PASS | "credit balance is too low" → circuit open, 이후 호출 즉시 차단 |
| `test_nvidia_auth_error_opens_circuit` | PASS | "invalid api key" → NVIDIA circuit open → Anthropic fallback 성공 |
| `test_circuit_open_blocks_subsequent_calls` | PASS | circuit open 상태 → `LlmCallError` 즉시 raise |
| `test_nvidia_circuit_triggers_anthropic_fallback` | PASS | NVIDIA circuit open → Anthropic으로 자동 전환 |
| `test_both_circuits_open_raises` | PASS | 양쪽 circuit 모두 open → `LlmCallError` |

**결론**: 영구 에러(크레딧 부족, 인증 실패) 감지 → 1시간 쿨다운 circuit breaker 정상 작동. 양쪽 다운 시 graceful failure.

---

## 5. LLM API 타임아웃/에러 Fallback

| 테스트 | 결과 | 설명 |
|--------|------|------|
| `test_nvidia_timeout_fallback_to_anthropic` | PASS | NVIDIA 타임아웃 → Anthropic fallback 성공 |
| `test_nvidia_error_then_anthropic_error_raises` | PASS | 양쪽 모두 500 에러 → `LlmCallError` |
| `test_retry_then_success` | PASS | 첫 호출 실패 → 재시도 성공 |
| `test_rate_limit_429_retry` | PASS | 429 rate limit → 더 긴 backoff 후 성공 |
| `test_rule_based_fallback_on_llm_failure` | PASS | LLM 완전 불가 시 rule-based fallback → 유효한 DecisionRecord 반환 |

**결론**: NVIDIA→Anthropic 이중 fallback + rule-based 최종 방어선 정상 작동.

---

## 6. 일간 손실 한도

| 테스트 | 결과 | 설명 |
|--------|------|------|
| `test_daily_loss_limit_blocks` | PASS | daily_loss_limit(10만원) 초과 시 매수 차단 |

---

## 7. Edge Cases & 견고성

| 테스트 | 결과 | 설명 |
|--------|------|------|
| `test_empty_headline_classification` | PASS | 빈 헤드라인 → UNKNOWN/IGNORE (crash 없음) |
| `test_very_long_headline` | PASS | 1000자+ 헤드라인 → 정상 분류 |
| `test_guardrail_state_position_count_never_negative` | PASS | 매도 > 매수 시 position_count = 0 (음수 방어) |
| `test_semaphore_prevents_thundering_herd` | PASS | 20건 동시 호출 → max 동시 실행 4건 이하 |
| `test_bought_tickers_dedup` | PASS | 동일 종목 중복 매수 시 set 정합성 유지 |

---

## 종합 평가

| 항목 | 상태 |
|------|------|
| max_positions 가드레일 | OK — 4건 초과 시 즉시 차단 |
| 섹터 집중 방어 | OK — 동일 섹터 2건 초과 차단 |
| 연속 손절 halt | OK — 3연속 후 매수 중단 |
| 뉴스 폭주 처리 | OK — 12건 동시 분류 < 1초, 중복 제거 정상 |
| LLM 동시성 제어 | OK — semaphore(4) 정상 작동, thundering herd 방지 |
| Circuit breaker | OK — 영구 에러 감지 → 1시간 차단, provider별 독립 |
| NVIDIA→Anthropic fallback | OK — 타임아웃/에러 시 자동 전환 |
| Rule-based fallback | OK — LLM 완전 불가 시 키워드 기반 결정 |
| 일간 손실 한도 | OK — 초과 시 매수 차단 |
| Edge case 방어 | OK — 빈 입력, 초장문, 음수 방어 |

**월요일 실거래 준비 완료. v82 모든 안전장치 정상 작동 확인.**
