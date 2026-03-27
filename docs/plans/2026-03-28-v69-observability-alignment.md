# 2026-03-28 v69 Observability Alignment

## Intent

v69 배포 이후 드러난 `/health` 상태 부정확성을 제거하고, 장중 운영자가 dashboard 한 화면에서 runtime feed heartbeat 와 실시간 trading metrics 를 신뢰할 수 있게 만든다.

## Problem

- 현재 watchdog/journal 은 `feed.last_poll_at` 를 기준으로 heartbeat 를 출력하지만 `/health.last_poll_at` 는 별도 타임스탬프를 사용한다.
- `pipeline.py` 의 `health_state.record_poll()` 는 non-empty batch 경로에서만 호출되어 빈 poll 동안 health 상태가 멈춘다.
- dashboard 는 로그 기반 intraday/weekly PnL 은 제공하지만, health API 기반 live trade KPIs 를 운영 surface 에 반영하지 않는다.

## Scope

1. `/health` 의 `last_poll_at` 를 feed heartbeat 소스와 일치시킨다.
2. 런타임 closed-trade 기준 실시간 승률, 총 P&L, 평균 수익률, MDD 를 계산해 health payload 로 제공한다.
3. dashboard 에 실시간 trade KPI 와 heartbeat freshness 를 반영한다.
4. 테스트, commit/push, remote deploy/검증까지 수행한다.

## Design

### Health Source Alignment

- `HealthState` 에 runtime feed reference 를 연결한다.
- `snapshot()` 은 `feed.last_poll_at` 를 우선 사용해 `last_poll_at` 을 직렬화한다.
- 기존 `record_poll()` 은 fallback/테스트 용도로 유지하되, primary source 는 feed heartbeat 로 전환한다.
- 추가로 `last_poll_source` 와 `last_poll_age_seconds` 를 포함해 dashboard 가 freshness 를 바로 표시할 수 있게 한다.

### Live Trade Metrics

- `PerformanceTracker` 에 intraday live metrics helper 를 추가한다.
- 계산 기준:
  - 대상: 현재 KST 영업일에 close 완료된 trades
  - win_rate: `pnl_pct > 0`
  - total_pnl_pct / total_pnl_won / avg_pnl_pct
  - peak_ret_pct / mdd_pct: cumulative `pnl_pct` 시계열 기준
- `HealthState` 가 `PerformanceTracker` reference 를 받아 `trade_metrics` 블록으로 expose 한다.

### Dashboard

- `dashboard/data_loader.py` 에 health payload 정규화 helper 를 추가한다.
- `dashboard/app.py`
  - 시스템 상태 탭에 heartbeat freshness + last_poll source 표시
  - 매매 성과 탭 상단에 live metrics 카드 추가
  - health 응답이 있으면 live metrics 를 우선 표시하고, 없으면 기존 로그 기반 계산을 유지

## Validation

- `python3 -m compileall src dashboard tests`
- `.venv/bin/python -m pytest tests/test_health.py tests/test_performance.py tests/test_dashboard.py -q`
- `.venv/bin/python -m pytest -q`
- affected file diagnostics 0
- remote deploy 후:
  - `systemctl is-active kindshot kindshot-dashboard`
  - `curl -sf http://127.0.0.1:8080/health`
  - `curl -I http://127.0.0.1:8501`

## Rollback

- 이전 known-good commit 을 clean export/rsync 하여 `/opt/kindshot` 에 덮어쓴 뒤 `.venv` 재설치와 서비스 restart 를 수행한다.
