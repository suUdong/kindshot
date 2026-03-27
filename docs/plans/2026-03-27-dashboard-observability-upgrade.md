# 2026-03-27 Dashboard Observability Upgrade

## Intent

대시보드를 "예쁜 요약 화면"이 아니라 장중 운영과 전략 리뷰에 바로 쓰는 관측면으로 끌어올린다. 이번 slice는 전략 변경이 아니라 가시화와 분석 surface를 강화하는 작업이다.

## Current State

- `dashboard/app.py` 가 대부분의 UI를 단일 파일에서 구성한다.
- `dashboard/data_loader.py` 는 이벤트/컨텍스트/PnL 멀티데이 로드만 제공한다.
- 기존 v8은 전략 제안과 멀티데이 분석까지 있지만 다음이 비어 있다.
  - intraday equity / drawdown
  - blocked BUY shadow snapshot 성과
  - version release trend
  - 최근 뉴스 feed monitor
  - 모바일용 layout tightening

## Evidence

- `scripts/backtest_analysis.py --dates 20260327`
  - executed BUY `5`
  - win rate `40.0%`
  - total P&L `-0.38%`
  - MDD `-2.77%`
- `docs/reports/performance_analysis_20260327.md`
  - baseline report with win rate `21.4%`, total return `-17.77%`
- `git show 485238d`
  - v65 baseline noted as win rate `35.7%`, cumulative `-3.66%`, MDD `-5.04%`

## Design

### Data Loader Layer

- add daily equity helper:
  - input: selected date
  - output: per-trade chronological rows with `cum_ret_pct`, `drawdown_pct`
- extend multi-day P&L helper:
  - add `peak_ret_pct`, `drawdown_pct`
- add shadow snapshot helpers:
  - raw join helper for blocked BUY + `shadow_` snapshots
  - summary helper for KPI-friendly aggregate
- add live feed helper:
  - recent N events across latest dates
- add version trend helper:
  - curated baseline rows with `version`, `win_rate`, `total_ret_pct`, `mdd_pct`, `source`, `notes`

### UI Layer

- inject CSS variables + responsive rules
- keep current tabs but enrich:
  - `매매 성과`
    - intraday equity
    - weekly equity
    - drawdown visualization
    - shadow snapshot section
    - version trend section
  - `시그널 현황`
    - live feed monitor card/table
- prefer charts + compact KPI rows over massive text blocks

### Mobile Strategy

- reduce page padding
- stack columns under 768px
- allow tables to collapse to smaller height
- keep plots full-width and shorter in height on small screens

## Risks

- shadow snapshot data may still be sparse; UI must clearly differentiate "feature absent" from "no opportunities".
- version trend is partly curated; source text must remain visible to avoid false precision.
- Streamlit column-heavy layout can still be tight on very narrow devices; CSS fallback should avoid unreadable overflow.

## Validation / Rollback

- run compile + dashboard tests + full pytest
- deploy with rsync path already used in this repo
- rollback by re-syncing prior tree and restarting services
