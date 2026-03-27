# 2026-03-27 Kindshot v69: 뉴스 프롬프트, 부분 익절, 동적 일손실 한도

## 목표

v68에서 추가된 증권사 리포트, 멀티 타임프레임, 종목 학습을 유지한 채, v69에서는 다음 세 가지를 하나의 실행 가설로 묶는다.

- LLM 프롬프트 입력과 규칙을 더 명시적으로 만들어 뉴스/공시 분류 정확도를 높인다.
- 포지션을 한 번에 전량 청산하지 않고 일부 수익을 먼저 잠그고 나머지는 더 세밀한 trailing으로 관리한다.
- 하루 손실 한도를 고정값이 아니라 intraday realized PnL과 연속 손실 상태에 맞춰 동적으로 조정한다.

## 현재 상태

- `decision_strategy.txt`는 강한 촉매 규칙이 있지만, 실제 프롬프트 입력은 기사/공시 판별 결과, 뉴스 카테고리, hold profile, 리스크 예산 같은 구조화 힌트를 충분히 담지 않는다.
- `price.py`는 한 포지션당 최종 청산 1회만 가정한다. partial take-profit이 없어 수익 잠금과 남은 물량 관리가 분리되지 않는다.
- `guardrails.py`는 `daily_loss_limit`, `daily_loss_limit_pct`를 정적으로 비교한다. 수익 일부 보호나 연속 손실 후 추가 tightening이 없다.
- `main.py`의 `_on_trade_close`는 realized PnL, stop-loss streak, 텔레그램 알림, 성과기록을 한 곳에서 묶어 처리한다. 따라서 exit 이벤트 shape를 좁게 확장하면 전체 bookkeeping을 유지할 수 있다.

## 설계

### 1. 뉴스 감성 분석 고도화

- `decision.py`에서 LLM 호출 전 이미 계산 가능한 구조화 힌트를 프롬프트에 추가한다.
- 추가 후보:
  - `is_direct_disclosure`
  - `is_commentary`
  - `is_broker_note`
  - `news_category`
  - `hold_profile`
  - `contract_amount_eok`
  - `dorg`
  - `daily_loss_budget` / `daily_pnl`
- `decision_strategy.txt`도 이 구조화 입력을 전제로 다시 정리한다.
- 목표는 "강한 확정 촉매 과소평가"와 "기사/리포트형 과대평가"를 동시에 줄이는 것이다.

### 2. 부분 익절 + 세분화 trailing

- `SnapshotScheduler` 내부에 이벤트별 남은 물량 비율과 partial exit 상태를 저장한다.
- 첫 목표가 hit 시:
  - 일부 물량만 실현
  - realized PnL을 즉시 반영
  - 포지션 카운트는 유지
  - 남은 물량에는 더 촘촘한 trailing profile 적용
- 최종 청산 시에만:
  - `record_sell`
  - stop-loss streak reset/increment
  - 포지션 종료 처리
- paper/live 공통 bookkeeping 경로는 유지하되, live에서도 가상 close bookkeeping은 동일한 이벤트 shape를 사용한다.

### 3. 동적 일손실 한도

- base loss limit를 그대로 두고, 그 위에 "effective daily loss floor"를 계산하는 helper를 추가한다.
- 동적 floor는 두 방향으로 움직인다.
  - 연속 손절이 늘수록 floor를 tighter하게 이동
  - 당일 realized profit이 쌓이면 일부 수익을 보호하도록 floor를 위로 끌어올림
- guardrail 차단 시에는 static 이유 대신 동적 floor와 현재 PnL이 드러나도록 로그를 남긴다.

## 로깅 / 관측성

- partial take-profit hit 로그
- remaining size / tightened trailing 로그
- dynamic daily loss floor 계산 로그
- Telegram sell 알림에 partial/final 구분

## 검증

1. compileall
2. touched-area pytest
3. full pytest
4. affected-file diagnostics
5. `kindshot-server` rsync 배포
6. remote compile/install/restart/smoke checks

## 롤백

- v69 커밋 revert 후 기존 배포 절차로 재동기화 및 `kindshot` 재시작
- `deploy/`, `.env`, live-order wiring은 유지되므로 rollback 범위는 좁다
