# KS v85.1 Feed Activation Procedure (2026-05-11)

Local paper daemon에서 v85.1 + 모든 통합 feed(News/Rebalance/Alpha/Y2i/DART PEAD/Macro)
를 활성화하는 절차. Producer side(y2i, alpha-scanner, macro-intelligence)는 별도 세션에서
이미 가동 중이므로 본 문서는 KS pipeline(consumer) 측 설정만 다룬다.

## 1. 사전 조건

```bash
# v85.1 commit 확인
cd /home/wdsr88/workspace/kindshot
git log --oneline -1
# expect: eb80ecf 또는 그 이후

# 패키지 재설치 (feeds/ 디렉토리 pickup 보장)
.venv/bin/pip install -e . --quiet

# 임포트 smoke
.venv/bin/python -c "from kindshot.feeds.rebalance_feed import RebalanceFeed; \
from kindshot.feed import AlphaFeed, Y2iFeed, DartFeed; \
from kindshot.config import Config; \
c = Config(); print('stagnation', c.stagnation_exit_enabled)"
```

## 2. 필수 환경변수 (`.env`)

### Core (이미 설정)
| Key | Value | 비고 |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | sk-ant-… | LLM 분류용. **잔액 부족 시 rule_fallback 모드로 동작** |
| `KIS_APP_KEY` / `KIS_APP_SECRET` / `KIS_ACCOUNT_NO` | (paper account) | KIS 거래 API |
| `KIS_IS_PAPER` | `true` | paper 모드 강제 |
| `LLM_MODEL` | `claude-haiku-4-5-20251001` | 비용 최소화 모델 |

### Feed source / News
| Key | Value | 효과 |
| --- | --- | --- |
| `FEED_SOURCE` | `KIS` (또는 `KIS,DART`) | 뉴스 소스 셀렉터 |
| `ANALYST_FEED_ENABLED` | `true` (default) | 애널리스트 리포트 |

### RebalanceFeed (Topic 2)
| Key | Value | 비고 |
| --- | --- | --- |
| `REBALANCE_FEED_ENABLED` | `true` (default) | 6/12월 windowing |

> 현재 시점(5월)은 윈도우 밖이므로 등록만 되고 시그널 0건이 정상.

### AlphaFeed (Topic 3, alpha-scanner producer)
| Key | Value | 효과 |
| --- | --- | --- |
| `ALPHA_FEED_ENABLED` | `true` | feed 등록 |
| `ALPHA_SCANNER_API_BASE_URL` | `http://127.0.0.1:8765` | alpha-scanner serve-kindshot-api 주소 |
| `ALPHA_FEED_POLL_INTERVAL_S` | `300` (default) | 5분 폴 |
| `ALPHA_FEED_MIN_CONFIDENCE` | `78` (default) | conf<78 BUY 차단 |

> Producer 측에서 `alpha-scanner serve-kindshot-api` 가 가동 중이어야 함. 현재 미가동 시
> kindshot consumer는 connection error 로그만 남기고 진행(non-fatal).

### Y2iFeed (Topic 1, y2i producer)
| Key | Value | 효과 |
| --- | --- | --- |
| `Y2I_FEED_ENABLED` | `true` | feed 등록 |
| `Y2I_SIGNAL_PATH` | `/home/wdsr88/workspace/y2i/.omx/state/kindshot_feed.json` (default) | 파일 기반 IPC |
| `Y2I_MIN_SCORE` | `55` (default) | 신호 점수 floor |
| `Y2I_MIN_VERDICT` | `WATCH` (default) | 베르딕트 floor |

> y2i 스케줄러(`run-scheduler`)가 kindshot_feed.json 을 주기적으로 갱신해야 함. 파일이
> 없으면 KS는 warning 로그 후 빈 결과로 진행.

### DART PEAD Phase 1 (earnings_queue 라우팅)
| Key | Value | 효과 |
| --- | --- | --- |
| `DART_API_KEY` | (40자 토큰) | **시크릿. .env에만 보관, 커밋 금지** |
| `DART_EARNINGS_ENABLED` | `true` (default) | 잠정실적 strategy |

> DART_API_KEY 미설정 시 DartFeed/DartEarningsStrategy 전부 비활성.

### Macro Filter (macro-intelligence producer)
| Key | Value | 효과 |
| --- | --- | --- |
| `MACRO_FILTER_ENABLED` | `true` (default) | guardrail 진입 차단 |
| `MACRO_API_BASE_URL` | `http://127.0.0.1:8000` | macro-intelligence FastAPI 주소 |
| `MACRO_API_TIMEOUT_S` | `5.0` (default) | fetch timeout |

> macro `serve` 가 가동 중이어야 함(현재 PID 628 가동 중).

### v85.1 stagnation_exit
| Key | Value | 비고 |
| --- | --- | --- |
| `STAGNATION_EXIT_ENABLED` | `true` (default) | 정체 청산 활성 |
| `STAGNATION_EXIT_MAX_PEAK_PCT` | `0.05` (default) | peak ≤ 0.05% |
| `STAGNATION_EXIT_MIN_MINUTES` | `15` (default) | t+15m 이후 적용 |

## 3. 활성화 절차 (로컬)

```bash
cd /home/wdsr88/workspace/kindshot
.venv/bin/pip install -e . --quiet

# (시크릿 외) 신규 flag 추가
cat >> .env <<'EOF'

# v85.1 feed enablement
ALPHA_FEED_ENABLED=true
ALPHA_SCANNER_API_BASE_URL=http://127.0.0.1:8765
Y2I_FEED_ENABLED=true
MACRO_API_BASE_URL=http://127.0.0.1:8000
EOF

# DART_API_KEY 는 사용자가 별도 echo 'DART_API_KEY=...' >> .env 로 주입
# .env 는 .gitignore 에 등록되어 있음 — 절대 commit 금지

# paper daemon 기동 (foreground 검증)
.venv/bin/python -m kindshot --paper 2>&1 | tee logs/v85.1-paper-$(date +%Y%m%d).log
```

## 4. 등록 로그 표본 (정상)

```
INFO  kindshot.main  RebalanceFeed registered (enabled=True)
INFO  kindshot.main  AlphaFeed registered (enabled=True, base_url=http://127.0.0.1:8765, min_confidence=78)
INFO  kindshot.feed  Y2iFeed enabled (path=..., min_score=55, interval=60s)
INFO  kindshot.feed  DartFeed initialized (earnings_queue routing active)
INFO  kindshot.main  MacroFilter registered (base_url=http://127.0.0.1:8000)
```

## 5. trade_history.db 모니터링

```bash
# DB 위치 (로컬 paper)
DB=/home/wdsr88/workspace/kindshot/data/trade_history.db

# 신규 진입 카운트 (재시작 이후)
sqlite3 "$DB" "SELECT date(entry_ts) d, COUNT(*) n FROM trades \
  WHERE entry_ts > datetime('now', '-1 day') GROUP BY d ORDER BY d DESC"

# 일별 exit_ret_pct 집계
sqlite3 "$DB" "SELECT date(exit_ts) d, COUNT(*) n, ROUND(AVG(exit_ret_pct),3) avg_pct \
  FROM trades WHERE exit_ts IS NOT NULL GROUP BY d ORDER BY d DESC LIMIT 14"

# v85.1 stagnation_exit 발동 trade
sqlite3 "$DB" "SELECT entry_ts, ticker, exit_reason, exit_ret_pct FROM trades \
  WHERE exit_reason LIKE '%stagnation%' ORDER BY exit_ts DESC LIMIT 20"
```

## 6. 트러블슈팅

- **`No module named 'kindshot.feeds'`** → `pip install -e .` 재실행
- **Anthropic credit too low** → LLM 호출 실패, pipeline은 rule_fallback 로 진행.
  paper 시뮬 정확도가 떨어지므로 credit 보충 권장.
- **AlphaFeed connection refused** → `cd ~/workspace/alpha-scanner && alpha-scanner serve-kindshot-api` 실행
- **Y2iFeed signal file not found** → y2i scheduler 가동 여부 확인 (PID 141851)
- **macro regime null** → macro `serve` 프로세스 (PID 628) 와 8000 포트 점검
- **DART feeds 전부 미동작** → `DART_API_KEY` 누락. .env에 추가 (commit 금지)

## 7. 002990-trap 회피 모니터링 (v85.1 risk)

stagnation_exit 으로 0% 청산했는데 그 trade 가 그 후 peak +1%+ 까지 갔으면 winner 차단.

```bash
sqlite3 "$DB" "SELECT entry_ts, ticker, exit_ret_pct, peak_pct_after_exit \
  FROM trades WHERE exit_reason LIKE '%stagnation%' \
  AND peak_pct_after_exit IS NOT NULL AND peak_pct_after_exit > 0.8 \
  ORDER BY entry_ts DESC LIMIT 20"
```

> `peak_pct_after_exit` 컬럼이 미존재하면 별도 backfill 필요. 임시로는 운영자가
> KIS chart 로 확인.
