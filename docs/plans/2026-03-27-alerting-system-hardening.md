# 2026-03-27 Alerting System Hardening

## Intent

이번 slice 는 전략을 바꾸는 작업이 아니라 운영 관측면을 Telegram 중심으로 닫는 작업이다. 목표는 "대시보드 확인용" 상태에서 끝나지 않고, 장중과 장마감 판단을 Telegram 만으로도 재구성할 수 있게 만드는 것이다.

## Current State

- `BUY` Telegram 알림은 이미 runtime 에 일부 연결되어 있다.
- `SELL`/청산은 `price.py` 내부에서 판단되지만 operator-facing 알림은 없다.
- guardrail 차단은 runtime 로그와 일부 고확신 skip alert 로만 보인다.
- `PerformanceTracker` 는 존재하지만 현재 runtime 메인 루프에 붙어 있지 않다.

## Decision

이번 bounded slice 는 세 알림면을 한 배치로 닫는다.

1. 실시간 `BUY`/`SELL` signal Telegram
2. 일일 성과 요약 자동 발송
3. guardrail 차단 알림 강화 (`shadow` 포함)

## Design

### Runtime hook strategy

- 진입 알림은 기존 `pipeline.py` 경로 유지
- 청산 알림은 `price.py` 의 virtual exit / close fallback 지점에서 일관되게 발송
- 거래 성과 기록도 같은 청산 hook 에 묶어 중복 계산을 피한다

### Daily summary strategy

- `main.py` 에 하루 1회 summary loop 추가
- close snapshot delay 이후 발송
- runtime state 파일로 중복 발송 방지
- Telegram 미설정 환경에서는 no-op

### Guardrail alert strategy

- blocked BUY 는 텔레그램으로 모두 알림
- `shadow` 스케줄 여부를 메시지에 직접 포함
- 기존 shadow scheduling threshold 자체는 바꾸지 않는다

## Validation

- formatter/unit tests 추가
- runtime integration tests 추가
- compile + full pytest

## Rollback

- 알림 훅과 formatter 확장만 되돌리면 기존 runtime 으로 복귀 가능
- 배포/secret/state contract 는 유지한다
