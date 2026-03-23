# Kindshot

KRX 뉴스 기반 데이 트레이딩 MVP. 실시간 공시/뉴스를 LLM으로 분석하여 BUY/SKIP 판단.

## Architecture

```
KIS API (뉴스/호가)
    |
    v
Feed → Bucket 분류 → Quant 필터 → LLM 판단 → Guardrails → Paper/Live 실행
    |                                              |
    v                                              v
UNKNOWN Review ← LLM 배치 분류          Price Tracker (TP/SL/Trailing Stop)
    |                                              |
    v                                              v
Keyword Feedback Loop                    Replay 시뮬레이션 + 성과 리포트
```

## Quick Start

```bash
# 1. 설치
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. 환경 설정
cp .env.example .env
# .env에 ANTHROPIC_API_KEY, KIS_APP_KEY 등 설정

# 3. 테스트
pytest tests/ -q

# 4. 실행 (paper 모드)
python -m kindshot --paper
```

## Docker 배포

```bash
# .env 파일 설정 후
docker compose up -d

# 헬스체크
curl http://localhost:8080/health
```

## 주요 명령어

```bash
# Paper trading
python -m kindshot --paper

# Replay (과거 로그)
python -m kindshot --replay logs/kindshot_20260316.jsonl

# Replay (날짜 기준)
python -m kindshot --replay-day 20260316

# Replay 배치 실행
python -m kindshot --replay-ops-cycle-ready --replay-ops-run-limit 5

# UNKNOWN 배치 리뷰
python scripts/unknown_batch_review.py --max-items 50

# 키워드 피드백
python scripts/unknown_keyword_feedback.py

# 로그 정리
python scripts/log_rotate.py --keep-days 7

# Historical backfill
python -m kindshot collect backfill --max-days 5
```

## 설정

모든 설정은 환경변수로 오버라이드 가능. 기본값은 `src/kindshot/config.py` 참조.

| 환경변수 | 기본값 | 설명 |
|---------|--------|------|
| `ANTHROPIC_API_KEY` | - | LLM API 키 (필수) |
| `KIS_APP_KEY` | - | KIS API 키 |
| `KIS_APP_SECRET` | - | KIS API 시크릿 |
| `KIS_IS_PAPER` | true | Paper trading 모드 |
| `CHASE_BUY_PCT` | 3.0 | 추격매수 차단 (당일 상승률) |
| `MIN_BUY_CONFIDENCE` | 72 | BUY 최소 confidence |
| `PAPER_TAKE_PROFIT_PCT` | 1.5 | TP 목표 |
| `PAPER_STOP_LOSS_PCT` | -1.0 | SL 한도 |
| `TRAILING_STOP_ENABLED` | true | Trailing stop 활성화 |
| `MAX_HOLD_MINUTES` | 30 | 최대 보유 시간 (분) |

## 전략 튜닝

LLM 프롬프트는 `src/kindshot/prompts/decision_strategy.txt`에 외부화.
코드 변경 없이 전략 실험 가능.

## 테스트

```bash
pytest tests/ -q           # 전체
pytest tests/ -x -q        # 첫 실패 시 중단
pytest tests/ -k "decision" # 특정 모듈
```

## 프로젝트 구조

```
src/kindshot/
  config.py          설정 (env var 오버라이드)
  main.py            런타임 파이프라인 + CLI
  feed.py            KIS 뉴스 폴링
  bucket.py          키워드 기반 버킷 분류
  decision.py        LLM BUY/SKIP 판단
  guardrails.py      매매 안전장치
  price.py           가격 추적 + TP/SL/Trailing Stop
  replay.py          과거 데이터 리플레이
  collector.py       Historical backfill
  unknown_review.py  UNKNOWN 이벤트 LLM 리뷰
  llm_client.py      공통 LLM 클라이언트 (retry/backoff)
  health.py          헬스체크 HTTP 서버
  errors.py          도메인별 예외 계층
  prompts/           외부화된 LLM 프롬프트
scripts/
  replay_sim.py              오프라인 수익률 시뮬레이션
  replay_batch_auto.py       리플레이 배치 자동화
  unknown_batch_review.py    UNKNOWN 배치 LLM 분류
  unknown_keyword_feedback.py  키워드 피드백 루프
  log_rotate.py              로그 로테이션
```

## License

Private. All rights reserved.
