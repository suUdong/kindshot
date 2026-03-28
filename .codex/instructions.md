# Kindshot - KRX News Day-Trading System

## Overview
KRX 뉴스/공시 기반 한국 주식 자동매매 시스템. 실시간 공시 수집 → 버킷 분류 → LLM 판단 → 가드레일 → 주문 실행.
전체 워크스페이스 맵: `~/workspace/WORKSPACE.md` 참조.

## Architecture
```
[KRX 공시 Feed] → collector → bucket → quant pre-filter
  → LLM decision → guardrails → order execution → price tracking
  → telegram notifications → daily summary
```

### Core Modules (`src/kindshot/`)
| Module | Role |
|--------|------|
| `pipeline.py` | 이벤트 처리 메인 파이프라인 |
| `collector.py` | KRX 공시 수집 + 백필 |
| `bucket.py` | 뉴스 카테고리 분류 (수주/계약/실적/지분 등) |
| `decision.py` | LLM 기반 매매 판단 (confidence, size_hint) |
| `guardrails.py` | 포트폴리오 리스크 가드레일 (스프레드/ADV/변동성) |
| `order.py` | KIS API 주문 실행 (시장가 매수/매도, 재시도) |
| `config.py` | 환경변수 + risk_limits.toml 기반 설정 |
| `trade_db.py` | SQLite 트레이드 히스토리 + 버전별 백테스트 |
| `telegram_ops.py` | 텔레그램 알림 (BUY/SELL/일일 요약/intraday) |
| `market.py` | KOSPI/KOSDAQ 시장 모니터링 |
| `quant.py` | 퀀트 지표 (RSI, ADV, 스프레드, 변동성) |
| `kis_client.py` | 한국투자증권 API 클라이언트 |
| `price.py` | 가격 스냅샷 추적 |

### Supporting Modules
| Module | Role |
|--------|------|
| `pattern_profile.py` | 과거 패턴 매칭 (손실 가드레일/수익 부스트) |
| `news_semantics.py` | 뉴스 시맨틱 분석 |
| `hold_profile.py` | 버킷별 홀딩 시간 프로필 |
| `ticker_learning.py` | 종목별 학습 (과거 성과 기반 보정) |
| `strategy_observability.py` | 전략 관측성 (exit 시뮬레이션) |

## Tech Stack
- Python 3.12+, asyncio
- KIS API (한국투자증권 OpenAPI)
- LLM: NVIDIA NIM (primary) / Anthropic Claude (fallback)
- SQLite (trade history), JSONL (event logs)
- Telegram Bot API (notifications)
- Streamlit (dashboard)

## Conventions
- Commit: `fix:`, `feat:`, `chore:` prefix. No emoji.
- Korean comments OK in domain logic
- Config defaults in `config.py`, override via env vars or `config/risk_limits.toml`
- `pytest -x -q` before commit

## Deploy
- Server: `kindshot-server` (AWS Lightsail, /opt/kindshot)
- systemd: `kindshot.service` (trading), `kindshot-dashboard.service` (Streamlit:8501)
- Deploy via rsync (preferred) or git pull

## Key Design Decisions
- **Dual LLM**: NVIDIA NIM primary (free tier), Anthropic fallback
- **Paper/Live mode**: paper mode 기본, micro-live는 금액 상한 적용
- **Position sizing**: confidence + 변동성 기반 동적 사이징
- **Trailing stop**: 시간대별 차등 (early/mid/late)
- **Risk limits**: max_positions, daily_loss_budget, consecutive_stop_loss 차단

## Known Limitations
- Sector guardrail inactive (pykrx has no sector API)
- VKOSPI fetch disabled (KRX blocks AWS IPs)

## Related Projects
- `macro-intelligence`: 매크로 레짐 시그널 (US/KR/Crypto)
- `alpha-scanner`: 텐배거 종목 포착
- `y2i`: 유튜브 인사이트 → 투자 시그널
