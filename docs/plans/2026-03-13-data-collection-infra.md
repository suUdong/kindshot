# Kindshot 데이터 수집 & 분석 인프라 설계
> 작성: Claude Opus 4.6 | 리뷰: @suUdong | 2026-03-13

## Context
현재 kindshot은 실시간 뉴스만 처리하고 버림. 체계적 데이터 수집이 없어서:
- 버킷 분류 정확도를 검증할 수 없음
- 뉴스→가격 상관관계 분석 불가
- LLM 판단 품질 측정 불가
- 전략 개선의 근거 데이터 부재

추가로 운영 제약이 명확하다:
- KIS 키는 실시간 파이프라인과 같은 자원을 쓴다
- KIS 뉴스는 장 마감 후에도 밤까지 계속 들어올 수 있다
- Lightsail 저사양 인스턴스라서 "빠른 대량 처리"보다 "느리지만 지속적인 백필"이 현실적이다

따라서 설계의 핵심은 **당일 데이터는 `kindshot run`이 끝까지 책임지고**, **야간/주말에는 이미 확정된 날짜를 `kindshot collect backfill`로 과거 방향으로 천천히 채우는 것**이다.

---

## 운영 원칙

### 1. 날짜 기준은 KST 고정

- 모든 수집 기준 날짜는 한국 시간(KST)으로 해석한다.
- 뉴스, 가격, 스냅샷, 판단 로그의 "day boundary"도 KST 기준으로 관리한다.

### 2. 당일과 과거를 섞지 않는다

- `kindshot run --mode paper|live`는 "오늘 날짜" 이벤트를 계속 수집한다.
- `kindshot collect backfill`은 "이미 확정된 날짜"만 처리한다.
- 같은 날짜를 runtime ingest와 backfill이 동시에 건드리지 않게 한다.

### 3. 장마감이 아니라 day-finalize 시점을 쓴다

- 장이 끝나도 뉴스/공시는 밤까지 이어질 수 있다.
- 따라서 `D일 데이터 = D+1 새벽 cutoff` 이후에만 확정한다.
- 권장 기본값: `finalize_cutoff_kst = 02:30`
- 예:
  - `2026-03-13` 데이터는 `2026-03-14 02:30 KST` 이후 finalized
  - 그 전까지는 `kindshot run`이 계속 수집

### 4. 저사양 서버에 맞춰 느리게 오래 돈다

- 목표는 "하루치씩 확실하게 채우는 것"이지, 짧은 시간에 수개월치를 끝내는 것이 아니다.
- 백필은 순차 처리 + 체크포인트 기반 재개를 기본으로 한다.

---

## 명령 구조

### 1. Runtime Command: `kindshot run`

목적:
- 오늘 날짜의 뉴스/판단/스냅샷을 실시간으로 처리하고 적재

하위 실행 모드:
- `dry_run`: 이벤트 로깅만 하고 판단/주문은 생략
- `paper`: 실시간 판단까지 수행하되 주문 실행은 생략
- `live`: 실제 운영 런타임 경로

특징:
- 현재 파이프라인 유지
- 장중 + 장후 뉴스까지 계속 수집
- 가능하면 실시간 호가/스프레드/시장 컨텍스트도 저장

### 2. Collection Command: `kindshot collect backfill`

목적:
- finalized된 과거 날짜를 역순으로 채우기

특징:
- 예: `20260310 -> 20260309 -> 20260308`
- 야간/주말 배치에 적합
- 느리게 처리해도 무방

### 3. Offline Command: `kindshot replay`

목적:
- `run`/`collect backfill`로 모은 데이터에 현재 처리 로직을 재실행

특징:
- `classify -> quant -> decision -> report`를 과거 데이터에 적용
- 전략 품질 검증용
- 수집과 판단을 분리해서 상태 관리 단순화

---

## Day Finalization 설계

### 왜 필요한가

- 장 마감 후에도 뉴스가 계속 들어온다.
- 같은 날짜를 너무 일찍 backfill하면, 나중에 들어온 기사/공시를 놓친다.

### 제안 규칙

- `today_kst`: 현재 KST 날짜
- `finalize_cutoff_kst`: 기본 `02:30`
- `finalized_date` 계산:
  - 현재 시각이 `02:30` 이전이면 `today - 2`
  - 현재 시각이 `02:30` 이후면 `today - 1`

예:
- `2026-03-14 01:00 KST` 실행 시 → finalized_date = `2026-03-12`
- `2026-03-14 03:00 KST` 실행 시 → finalized_date = `2026-03-13`

### 결과

- `kindshot run`은 항상 `today`만 담당
- `kindshot collect backfill`은 항상 `cursor_date <= finalized_date`만 담당
- 오늘 날짜와 backfill 수집 범위가 충돌하지 않음

---

## 데이터 소스 전체 맵

### A. 과거 수집 가능 (배치)
| 데이터 | 소스 | API/방법 | 제약 |
|--------|------|----------|------|
| 뉴스/공시 | KIS API | `FID_INPUT_DATE_1` 조작 | 30~90일? 한도 미확인 |
| 일봉 OHLCV | pykrx | `get_market_ohlcv_by_date()` | 수년치 가능 |
| 일별 KOSPI/KOSDAQ | pykrx | `get_index_ohlcv_by_date()` | 수년치 가능 |
| 일별 거래대금/시총 | pykrx | `get_market_cap_by_date()` | 수년치 가능 |

### B. 실시간만 가능 (runtime ingest)
| 데이터 | 소스 | 현재 상태 | 비고 |
|--------|------|-----------|------|
| 호가/스프레드 | KIS `inquire-asking-price` | 파이프라인에서 조회 후 버림 | DB에 쌓으면 스프레드 분석 가능 |
| 분봉 가격 | KIS `inquire-price` | T+0/5/10/30 스냅샷만 | 더 촘촘히 쌓을 수도 |
| LLM 판단 결과 | Anthropic API | JSONL 로그에 기록 중 | 이미 있음, DB 이관 가능 |
| 이벤트 전체 로그 | 파이프라인 | JSONL로 기록 중 | 이미 있음 |
| 시장 컨텍스트 | MarketMonitor | 메모리에만 | DB에 쌓으면 시장 상태 분석 가능 |

### C. 기존 로그에서 추출 가능 (마이그레이션)
| 데이터 | 소스 | 비고 |
|--------|------|------|
| 과거 이벤트/판단 | `logs/*.jsonl` | 파싱해서 DB에 넣으면 SQL 분석 가능 |
| unknown 헤드라인 | `logs/unknown_headlines/` | 버킷 튜닝 근거 |

---

## 모듈 1: Historical Collector (배치 수집기)

### 목적
- finalized된 과거 날짜를 역순으로 수집
- 저사양 서버에서도 밤/주말에 꾸준히 누락 날짜를 채움

### 운영 모델
- 독립 프로세스/서브커맨드: `kindshot collect backfill`
- 실행 시점:
  - 평일 야간: `kindshot run` 부하가 낮은 시간대
  - 주말: 장시간 backfill
- 같은 KIS 앱키 사용
- 단, 오늘 날짜는 절대 수집하지 않고 `finalized_date` 이하만 처리

### 수집 대상
1. **뉴스/공시**: KIS API date 파라미터로 과거 날짜 조회
2. **일봉 OHLCV**: 뉴스에 등장한 티커의 당일+익일 가격
3. **지수 데이터**: KOSPI/KOSDAQ 일봉
4. **시총/거래대금**: pykrx 기본 데이터

### 과거 뉴스 수집 전략
- `FID_INPUT_DATE_1 = "00YYYYMMDD"` + `FID_INPUT_HOUR_1 = "235959"` → 해당일 최신부터
- `tr_cont` 페이지네이션 (최대 10페이지)
- 10페이지 다 차면 → time-windowed crawl (응답 중 최소 시간으로 재쿼리)
- news_id 기준 중복 제거
- 날짜 단위 완료 후 `collection_state.cursor_date -= 1 day`
- 네트워크/토큰/일시적 KIS 오류는 날짜 단위로 소수 회 재시도 후 실패 처리한다.
- 재시도는 그 날짜의 뉴스 fetch 경로에만 우선 적용하고, 가격/지수 수집은 기존 fail-fast를 유지해 원인 구분을 단순화한다.
- 한 날짜가 최종 실패하면 cursor는 그 날짜에 머물러 다음 실행에서 재개되게 한다.
- KIS 뉴스가 최대 페이지 한도에서 멈추면 같은 날짜 안에서 응답의 최소 `data_tm`보다 1초 이전 시각으로 창을 내려 continuation을 시도한다.
- continuation으로 더 이상 시각을 내릴 수 없거나 여전히 한도에 걸린 채 끝나면 그 날짜는 `partial` 상태로 기록해 후속 recollect 판단 근거를 남긴다.

### Backfill 진행 방식

- 시작 커서 예: `20260310`
- `20260310` 수집 완료 시 `20260309`로 이동
- 다음 실행에서 이어서 계속 진행
- 이미 완료된 날짜는 재수집하지 않음
- 부분 실패 시 같은 날짜를 재시도
- 재시도 정책의 1차 기본값:
  - `max_attempts = 3`
  - `delay = 1s`, 선형 backoff
  - 모든 시도 실패 시 해당 날짜를 `error`로 기록하고 런을 중단

재개 규칙:
- collection log에서 최신 상태가 `complete` 또는 `skipped`인 날짜는 다음 backfill 실행에서 skip한다.
- 최신 상태가 `partial`인 날짜는 skip하지 않고 다시 시도한다.
- 최신 상태가 `error`인 날짜는 skip하지 않고 다시 시도한다.
- force-recollect 같은 명시적 override는 후속 단계로 남긴다.
- skip된 날짜도 `collection_log`에 `status="skipped"`로 남겨서 재개 판단과 운영 리뷰에서 구분 가능하게 한다.
- `skipped` 레코드에는 가능한 한 `skip_reason`을 함께 남겨서 "already_complete" 같은 운영 원인을 구분 가능하게 한다.
- skip된 완료 날짜를 지나면 `cursor_date`도 다음 과거 날짜로 전진시켜, 재실행 시 같은 완료 구간을 계속 다시 훑지 않게 한다.
- `partial` 날짜는 `last_completed_date`로 승격하지 않고 `cursor_date`도 그 날짜에 남겨서, 다음 기본 실행에서 자동 재시도되게 한다.
- backfill 결과 요약과 CLI 완료 로그는 `processed` 안에서 `complete`와 `partial`을 따로 구분해 보여주어, 운영자가 실제 완료분과 재시도 필요분을 즉시 구분할 수 있어야 한다.
- collector 내부 helper는 `collection_log`를 한 번만 읽고 최신 per-date outcome과 현재 `partial/error` backlog를 함께 제공해, resume 로직과 운영 리뷰가 JSONL 파싱을 중복 구현하지 않게 한다.

### Collector 상태 파일

예시: `data/collector_state.json`

```json
{
  "command": "collect",
  "task": "backfill",
  "cursor_date": "20260310",
  "last_completed_date": "20260311",
  "finalized_date": "20260313",
  "status": "idle",
  "updated_at": "2026-03-14T03:10:00+09:00"
}
```

필드 의미:
- `cursor_date`: 다음으로 수집할 과거 날짜
- `last_completed_date`: 가장 최근 성공 날짜
- `finalized_date`: 현재 시점에 안전하게 처리 가능한 최신 날짜
- `status`: `idle | running | error`

### Replay-Facing Storage Contract

- `collect backfill`은 처리한 날짜마다 `data/collector/manifests/YYYYMMDD.json` manifest를 남긴다.
- `collect backfill`은 `data/collector/manifests/index.json`도 함께 유지해, replay/analysis가 manifest 디렉터리를 직접 스캔하지 않고 available day를 열거하게 한다.
- manifest는 replay/analysis가 `collection_log` JSONL을 다시 스캔하지 않고도 해당 날짜의 최신 collector 결과를 바로 찾게 하는 read contract다.
- 최소 포함 필드:
  - `date`, `status`, `status_reason`, `has_partial_data`, `generated_at`, `finalized_date`
  - `counts.news`, `counts.classifications`, `counts.daily_prices`, `counts.daily_index`
  - `paths.news`, `paths.classifications`, `paths.daily_prices`, `paths.daily_index`
  - `tickers`: 그 날짜 뉴스에서 식별된 unique ticker 목록
  - `news_range.first_news_id`, `news_range.last_news_id`, `news_range.start_time`, `news_range.end_time`
  - `sources.news`, `sources.classifications`, `sources.daily_prices`, `sources.daily_index`
  - `exists.news`, `exists.classifications`, `exists.daily_prices`, `exists.daily_index`
- manifest는 `complete`와 `partial` 날짜에 대해 기록한다.
- `skipped` 날짜는 기존 manifest를 재사용하므로 새 manifest write를 강제하지 않는다.
- replay/analysis는 우선 manifest를 entrypoint로 사용하고, 세부 데이터는 manifest가 가리키는 경로에서 읽는다.
- `partial` 날짜는 manifest만 읽어도 왜 불완전한지 알 수 있게 `status_reason`을 채워야 한다. 현재 1차 값은 `pagination_truncated`다.
- `index.json`은 최소한 `generated_at`과 날짜별 엔트리 목록을 포함해야 하며, 각 엔트리는 `date`, `status`, `has_partial_data`, `manifest_path`, `generated_at`를 가져야 한다.
- replay 쪽 helper는 우선 `index.json`에서 available date를 읽고, 필요 시 각 날짜 manifest를 열어 dataset path와 status metadata를 해석해야 한다.

### CLI
```
kindshot run --mode paper
kindshot run --mode live
kindshot collect backfill
kindshot collect status
kindshot collect status --limit 5
kindshot collect status --json
kindshot collect status --json --output data/collector/status.json
kindshot collect backfill --cursor 20260310
kindshot collect backfill --from 20260301 --to 20260313
kindshot replay --date 20260310
```

입력 규칙:
- `--cursor YYYYMMDD`: 그 날짜부터 finalized_date까지 역방향으로 진행
- `--from YYYYMMDD --to YYYYMMDD`: 두 날짜 사이를 순서 무관하게 받아 내부에서 최신→과거 순으로 정규화
- `--from`만 주면 해당 날짜부터 finalized_date까지
- `--to`만 주면 `cursor_date` 또는 finalized_date부터 해당 날짜까지
- `--cursor`와 `--from/--to` 혼용은 허용하지 않는다
- `kindshot collect status`는 collector state와 `collection_log` 최신 요약을 읽어 idle/running/error 상태, 현재 cursor/finalized date, 최신 `partial`/`error` backlog 개수를 로그로 출력한다.
- `kindshot collect status --limit N`은 backlog 날짜 목록 로그를 최신순 N건으로 제한한다. 요약 카운트는 전체 backlog 기준을 유지한다.
- `kindshot collect status --json`은 같은 요약/상세 신호를 기계가 읽기 쉬운 JSON으로 stdout에 출력한다.
- `kindshot collect status --json --output PATH`는 같은 JSON payload를 stdout과 지정 파일에 함께 기록해 외부 polling/cron이 안정적으로 읽게 한다.
- `kindshot collect status`는 제한된 backlog 날짜에 대해 최신 record detail도 함께 출력해, `partial`은 `skip_reason`, `error`는 `error` 메시지와 카운트 필드를 바로 확인할 수 있어야 한다.
- `kindshot collect status`의 첫 summary line은 전체 tracked 날짜 수와 가장 오래 막힌 `partial/error` 날짜를 함께 보여줘, 세부 backlog 로그를 펼치지 않아도 현재 누락 위험과 우선순위를 판단할 수 있어야 한다.
- `kindshot collect status`의 첫 summary line은 막힌 날짜들의 최신 record 기준 aggregate count도 함께 보여줘, backlog가 단순히 몇 일치인지뿐 아니라 대략 어느 정도 수집/분류 잔량이 걸려 있는지도 판단할 수 있어야 한다.
- `--json` 출력은 state, summary aggregates, 최신 blocked backlog date lists, 제한된 detail records를 함께 포함하되, human log와 동일하게 `--limit` 상한을 존중한다.
- status report는 compact `health` label도 포함해야 한다. 예: 막힘이 없으면 `healthy`, `partial`만 있으면 `partial_backlog`, `error`가 있으면 `error_backlog`, collector state 자체가 `error`면 `collector_error`.
- status report는 blocked 날짜들의 최신 `completed_at` 기준 stale-age 신호도 포함해야 한다. 최소한 가장 오래된 blocked evidence의 age(초)와 summary 기준 시각을 같이 제공해 운영자가 backlog 신선도를 바로 판단할 수 있어야 한다.

---

## 모듈 2: Runtime Ingest Sink (실시간 DB 저장)

### 목적
`kindshot run` 파이프라인에서 이미 조회하는 데이터를 버리지 않고 DB에 축적

### 저장 대상
1. **호가 스냅샷**: 이벤트 처리 시 `build_context_card()`에서 이미 조회 → spread_bps, ask/bid 저장
2. **가격 스냅샷**: `SnapshotScheduler`가 이미 T+0/5/10/30 찍음 → DB에도 저장
3. **시장 컨텍스트**: `MarketMonitor.snapshot` (KOSPI/KOSDAQ 변동률, breadth) → 주기적 저장
4. **이벤트+판단**: 현재 JSONL → DB에도 write (dual-write 또는 JSONL→DB 배치 이관)

### 구현 방식
- `DbSink` 클래스: 파이프라인에 훅으로 추가
- 비동기 쓰기 (이벤트 처리 지연 방지)
- 실시간 파이프라인 성능에 영향 없어야 함

### 왜 필요한가

과거 백필만으로는 아래 데이터를 정확히 복원하기 어렵다:
- 당시 실시간 호가/스프레드
- 당시 주문장 top level 유동성
- 당시 시장 breadth의 세밀한 상태

즉, **과거 가격/뉴스는 `collect backfill`**, **미시구조는 runtime ingest sink**가 정답이다.

### 리스크
- DB 쓰기 실패가 트레이딩에 영향 주면 안 됨 → fire-and-forget or 별도 큐
- 디스크 용량: 일 1000건 뉴스 + 스냅샷 → SQLite로 수개월 OK

---

## 모듈 3: Log Migrator (기존 로그 이관)

### 목적
지금까지 쌓인 JSONL 로그를 DB로 이관해서 SQL 분석 가능하게

### 대상
- `logs/*.jsonl` → events, decisions 테이블
- `logs/unknown_headlines/*.jsonl` → unknown_headlines 테이블

### 구현
- 일회성 스크립트: `python -m kindshot.migrate_logs`
- idempotent (중복 실행 안전)

---

## 모듈 4: Analysis Toolkit

### 목적
수집된 데이터 기반 전략 검증 도구

### 분석 항목
- **버킷 정확도 감사**: 과거 헤드라인에 `classify()` 돌려서 분포 확인
- **신호 검증**: POS_STRONG 뉴스 + 당일/익일 가격 변동 상관관계
- **키워드 발굴**: UNKNOWN 버킷 헤드라인 패턴 분석
- **LLM 리플레이**: 수집된 뉴스로 오프라인 판단 재실행
- **전략 백테스트**: 뉴스+가격 DB로 시뮬레이션
- **LLM 판단 정밀도/재현율**

---

## 통합 DB 스키마

저장소: `data/kindshot.db` (단일 SQLite)

```sql
-- 모듈 1: 과거 뉴스
CREATE TABLE news (
    news_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,           -- YYYYMMDD
    time TEXT NOT NULL,           -- HHMMSS
    title TEXT NOT NULL,
    dorg TEXT,
    provider_code TEXT,
    ticker1 TEXT, ticker2 TEXT, ticker3 TEXT, ticker4 TEXT, ticker5 TEXT,
    source TEXT DEFAULT 'collector',  -- collector | runtime
    collected_at TEXT NOT NULL
);

-- 모듈 1+2: 버킷 분류 결과 (수집 후 오프라인 분류 or 라이브 분류)
CREATE TABLE classifications (
    news_id TEXT PRIMARY KEY REFERENCES news(news_id),
    bucket TEXT NOT NULL,
    keyword_hits TEXT,            -- JSON array
    classified_at TEXT NOT NULL
);

-- 모듈 1: 일봉 가격 (pykrx)
CREATE TABLE daily_prices (
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume INTEGER,
    value REAL,                   -- 거래대금
    market_cap REAL,              -- 시가총액
    collected_at TEXT NOT NULL,
    PRIMARY KEY (ticker, date)
);

-- 모듈 1: 지수
CREATE TABLE daily_index (
    index_code TEXT NOT NULL,     -- 0001=KOSPI, 2001=KOSDAQ
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume INTEGER,
    up_count INTEGER, down_count INTEGER, flat_count INTEGER,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (index_code, date)
);

-- 모듈 2: 실시간 호가 스냅샷
CREATE TABLE orderbook_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    ts TEXT NOT NULL,             -- ISO timestamp
    event_id TEXT,                -- 연결된 이벤트
    askp1 REAL, bidp1 REAL,
    ask_size1 INTEGER, bid_size1 INTEGER,
    total_ask_size INTEGER, total_bid_size INTEGER,
    spread_bps REAL
);

-- 모듈 2: 실시간 가격 스냅샷 (T+0/5/10/30)
CREATE TABLE price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    ts TEXT NOT NULL,
    offset_minutes INTEGER,       -- 0, 5, 10, 30
    price REAL,
    volume INTEGER,
    source TEXT DEFAULT 'scheduler'
);

-- 모듈 2: 시장 컨텍스트 타임시리즈
CREATE TABLE market_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kospi_change_pct REAL,
    kosdaq_change_pct REAL,
    kospi_breadth_ratio REAL,
    kosdaq_breadth_ratio REAL
);

-- 모듈 2+3: 이벤트 로그
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT,
    mode TEXT,
    detected_at TEXT,
    ticker TEXT,
    corp_name TEXT,
    headline TEXT,
    bucket TEXT,
    skip_stage TEXT,
    skip_reason TEXT,
    quant_passed INTEGER,
    source TEXT                   -- KIND | KIS
);

-- 모듈 2+3: LLM 판단
CREATE TABLE decisions (
    event_id TEXT PRIMARY KEY REFERENCES events(event_id),
    action TEXT,                  -- BUY | SKIP
    confidence INTEGER,
    size_hint TEXT,
    reason TEXT,
    decided_at TEXT
);

-- 수집 진행 추적
CREATE TABLE collection_log (
    date TEXT PRIMARY KEY,
    news_count INTEGER,
    status TEXT NOT NULL,         -- complete | partial | error
    completed_at TEXT
);
```

---

## 구현 우선순위

### Phase 0: Feasibility Probe
- KIS 과거 뉴스 date 조회가 실제로 어느 범위까지 가능한지 검증
- KIS historical price endpoint를 collector 용도로 분리 가능한지 검증
- finalize cutoff가 필요한 실제 뉴스 유입 시각 분포 확인
- **가치**: collector 구현 전에 "가능한 것"과 "runtime ingest에만 남겨야 할 것"을 분명히 함

### Phase 1: Historical Collector
- `collector.py` 신규 (독립 모듈, ~250줄)
- `kis_client.py`에 `get_news_for_date()` / historical price wrapper 추가
- `config.py`에 `collector_db_path` 추가 (~3줄)
- `tests/test_collector.py` (~100줄)
- `collector_state` / finalized_date 계산 추가
- **가치**: 과거 뉴스 축적 시작, 버킷 정확도 검증 가능, 날짜 역순 backfill 가능

### Phase 2: Log Migrator
- 기존 JSONL → DB 이관 스크립트
- **가치**: 지금까지 데이터 살리기

### Phase 3: Runtime Ingest Sink
- 파이프라인에 DB write 훅 추가
- **가치**: 앞으로의 실시간 데이터 축적 (호가, 분봉 등)

### Phase 4: Analysis Toolkit
- 버킷 분류 정확도 리포트
- 뉴스→가격 상관관계 분석
- LLM 판단 정밀도/재현율

---

## 권장 1차 운영안

저사양 Lightsail 기준으로는 아래처럼 작게 시작하는 것이 적절하다.

1. `kindshot run --mode paper`
- 오늘 뉴스/이벤트/판단/가격 스냅샷 계속 수집

2. `kindshot collect backfill`
- 밤/주말에 `finalized_date` 이하 날짜를 하루씩 역순 수집
- 예: `20260310 -> 20260309 -> 20260308`

3. `kindshot replay`
- 수집이 끝난 날짜만 골라 현재 로직을 재실행

이렇게 시작하면 큰 인프라 없이도:
- 과거 데이터 축적
- 버킷/LLM 검증
- 가격 성과 분석
- 추후 미시구조 저장 확장

을 순서대로 진행할 수 있다.
