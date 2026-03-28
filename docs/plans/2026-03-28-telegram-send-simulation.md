# 2026-03-28 Telegram Send Simulation

## Intent

이번 slice 는 Telegram 운영 알림 자체를 바꾸는 것이 아니라, 운영자가 토큰 없이도 실제 전송 코드 경로를 검증할 수 있는 테스트 표면을 추가하는 작업이다.

## Current State

- `scripts/test_telegram.py --dry-run` 은 formatter 출력만 확인한다.
- 실제 `send_telegram_message()` 경로는 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` 가 없으면 검증할 수 없다.
- 단위 테스트는 `urlopen` monkeypatch 로 request shape 을 확인하지만, 운영자가 실행 가능한 CLI 시뮬레이션 경로는 없다.

## Decision

이번 bounded slice 는 다음만 추가한다.

1. `send_telegram_message()` 에 주입 가능한 opener 확장
2. `scripts/test_telegram.py --simulate-send` 모드
3. 토큰 없이도 request 생성, payload 직렬화, 응답 파싱까지 통과하는 자동 테스트

## Design

### Transport injection

- 기본 동작은 기존 stdlib `urlopen` 유지
- 테스트/시뮬레이션에서만 opener 를 주입해 네트워크 없이 동일한 request-building 경로를 통과시킨다
- 운영 runtime (`main.py`, `pipeline.py`) 호출부는 수정하지 않는다

### CLI simulation contract

- `--simulate-send` 는 환경변수 없이 실행 가능해야 한다
- 시뮬레이션은 fake bot token / chat id 를 사용하되 실제 `send_telegram_message()` 를 호출한다
- 출력에는 simulated status 와 request 핵심 정보(url, chat_id, timeout)를 남겨 operator 가 경로 검증 결과를 바로 볼 수 있게 한다

## Validation

- `pytest tests/test_telegram_ops.py tests/test_telegram_script.py`
- `python -m compileall src scripts/test_telegram.py tests/test_telegram_script.py`
- `python scripts/test_telegram.py --simulate-send --type buy`

## Rollback

- `scripts/test_telegram.py` 의 시뮬레이션 모드와 opener 주입만 되돌리면 기존 동작으로 복귀한다
- 실운영 Telegram 전송 contract 와 secret handling 은 유지된다
