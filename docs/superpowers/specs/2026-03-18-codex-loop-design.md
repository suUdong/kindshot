# Codex Loop: 자가 개선 엔진 설계

## 개요

장 마감 후 자동 실행되는 플러그인 기반 엔진으로, 두 가지 핵심 기능을 수행한다:

1. **언노운 키워드 자동 패치** — LLM 리뷰 결과에서 반복 등장하는 키워드를 bucket.py에 자동 추가
2. **Buy 판정 자가 개선** — 일일 통계 분석(규칙 기반) + 주간 심층 분석(Claude Code CLI)으로 시스템을 지속 개선

API 직접 호출 없이, Claude Code CLI를 활용한다.

## 아키텍처

```
src/kindshot/codex_loop/
  ├─ engine.py          # 엔진 코어: 플러그인 로드, 실행, 결과 수집
  ├─ plugin_base.py     # 플러그인 인터페이스 (ABC)
  ├─ plugins/
  │   ├─ keyword_patch.py    # 언노운 키워드 자동 패치
  │   ├─ daily_review.py     # 일일 통계 분석
  │   └─ weekly_deep.py      # 주간 심층 분석 (Claude Code CLI)
  ├─ git_ops.py         # git commit/branch/revert 유틸
  └─ telegram.py        # 알림 전송 (기존 telegram_ops 재사용)
```

### 엔진 실행 흐름

```
cron(장 마감 후) → engine.run(schedule="daily")
  1. 로그 수집 (오늘 이벤트/결정/가격 스냅샷)
  2. 등록된 daily 플러그인 순차 실행
  3. 각 플러그인이 반환: PluginResult(changes=[], alerts=[], report="")
  4. changes가 있으면: git commit + 패치 로그 JSONL
  5. Telegram 알림 (후보 목록 + 리포트 요약)
  6. 거부 기간 대기 (다음 배치까지)

cron(주말) → engine.run(schedule="weekly")
  - weekly 플러그인 추가 실행
  - Claude Code CLI로 심층 분석
  - 코드 변경 시: branch → pytest → 통과 시 자동 머지
```

### 플러그인 인터페이스

```python
class CodexPlugin(ABC):
    schedule: str  # "daily" | "weekly"

    @abstractmethod
    def run(self, context: RunContext) -> PluginResult: ...
```

`RunContext`는 오늘/이번 주 로그 경로, config 값, 이전 패치 이력 등을 포함한다.

`PluginResult`는 `changes: list[Change]`, `alerts: list[str]`, `report: str`을 반환한다.

## 선행 작업: bucket.py 마커 삽입

구현 전, bucket.py의 각 패치 대상 키워드 리스트 끝에 마커를 삽입한다:

```python
# 예시: POS_STRONG_KEYWORDS
POS_STRONG_KEYWORDS: list[str] = [
    "기존 수동 키워드들...",
    # --- auto-patch below ---
]
```

대상 리스트: `POS_STRONG_KEYWORDS`, `NEG_STRONG_KEYWORDS`, `POS_WEAK_KEYWORDS`, `NEG_WEAK_KEYWORDS`, `IGNORE_KEYWORDS`.
`IGNORE_OVERRIDE_KEYWORDS`는 특수 우선순위 리스트이므로 자동 패치 대상에서 제외한다.

## 플러그인 1: 키워드 패치 (keyword_patch.py)

### 기존 코드와의 관계

`unknown_review.py`에 이미 `unknown_review_rule_patch()` 함수와 `_PATCHABLE_BUCKET_KEYWORD_LISTS` 매핑이 존재한다.
keyword_patch 플러그인은 **이 기존 인프라를 래핑**한다:
- 후보 집계: 기존 `unknown_review_rule_queue` 로직 재사용
- 패치 실행: 기존 `_PATCHABLE_BUCKET_KEYWORD_LISTS` 매핑 재사용
- 추가 기능: 거부 메커니즘, Telegram 알림, git commit, 패치 로그

### 입력

- `logs/unknown_review/YYYY-MM-DD.jsonl` — LLM 리뷰 결과
  - 필드명: `suggested_bucket`, `keyword_candidates` (모델 `UnknownReviewRecord` 기준), `confidence`
- 누적 기간: 마지막 패치 이후 ~ 오늘

### 패치 조건

- 동일 키워드 **3회 이상** 등장 (`codex_keyword_min_occurrences`)
- 평균 confidence ≥ **85** (`codex_keyword_min_confidence`)
- bucket.py에 이미 존재하지 않을 것
- 이전에 거부된 키워드(`rejected_keywords.jsonl`)가 아닐 것
  - 거부 키워드는 TTL 기반 만료: 기본 90일 후 재제안 가능 (`codex_keyword_reject_ttl_days`)

### 패치 방식

1. bucket.py 읽기
2. `# --- auto-patch below ---` 마커 탐색
3. 마커 바로 위(리스트 닫는 `]` 직전)에 `"키워드",` 줄을 append
4. 마커 매칭 실패 시: 에러 로깅 + 해당 버킷 패치 스킵 (다른 버킷은 계속 진행)
5. 파일 쓰기

대상 버킷 제한 없음 — LLM이 제안하는 버킷이면 모두 가능 (POS_STRONG, NEG_STRONG, POS_WEAK, NEG_WEAK, IGNORE).
`IGNORE_OVERRIDE`는 자동 패치 대상에서 제외.

### 출력

```python
PluginResult(
    changes=[Change(file="bucket.py", description="Add 2 keywords to POS_STRONG")],
    alerts=["POS_STRONG: 대규모수주, 자사주매입결정"],
    report="3일간 unknown 45건 중 2건 키워드 후보 확정"
)
```

### 패치 로그

`logs/codex_loop/keyword_patches.jsonl`:
```json
{"ts": "...", "keyword": "대규모수주", "bucket": "POS_STRONG",
 "occurrences": 5, "avg_confidence": 92, "commit": "abc123"}
```

### 롤백

git revert로 해당 커밋만 되돌리면 키워드 제거됨.

## 플러그인 2: 일일 통계 분석 (daily_review.py)

### 입력

- 이벤트 레코드 (버킷, 스킵 사유)
- 결정 레코드 (BUY/SKIP, confidence)
- 가격 스냅샷 (t0, t+1m, t+5m, t+30m, close)
- SKIP 종목의 가격 변동 (놓친 기회)

### 분석 항목

| 분석 | 방법 | 출력 |
|------|------|------|
| BUY 수익률 분포 | t+5m, t+30m, close 수익률 통계 | 평균, 중위, 승률 |
| 퀀트 체크 효과 | 스킵 사유별 "만약 통과했으면" 수익률 | 각 체크의 필터링 가치 |
| 놓친 기회 | SKIP/UNKNOWN 중 close 기준 +3% 이상 | 종목, 헤드라인, 상승폭 |
| 임계값 민감도 | ADV, 스프레드 등 ±20% 변동 시 통과/차단 변화 | 현재값 vs 최적 구간 제안 |

### 출력

- 변경 없음 (리포트만 생성)
- 리포트 저장: `logs/codex_loop/daily/YYYY-MM-DD.md`
- Telegram으로 요약 알림

### 일일 리포트 포맷

주간 심층 분석이 이 파일을 입력으로 사용하므로, 파싱 가능한 구조로 작성한다:

```markdown
# Daily Review YYYY-MM-DD

## Summary
- total_events: N
- buy_count: N
- skip_count: N
- win_rate_close: N%

## BUY Returns
| ticker | headline | ret_5m | ret_30m | ret_close |
|--------|----------|--------|---------|-----------|

## Missed Opportunities
| ticker | headline | bucket | skip_reason | ret_close |
|--------|----------|--------|-------------|-----------|

## Threshold Sensitivity
| param | current | if_minus_20pct | if_plus_20pct | note |
|-------|---------|----------------|---------------|------|
```

## 플러그인 3: 주간 심층 분석 (weekly_deep.py)

### 입력

- 일일 리포트 7일치 (`logs/codex_loop/daily/*.md`)
- 해당 주 전체 이벤트/결정/가격 로그
- 현재 config.py 임계값
- 현재 bucket.py 키워드 목록
- LLM 프롬프트 파일

### 실행 방식

```
1. 주간 데이터를 요약 컨텍스트로 정리
2. Claude Code CLI 호출:
   claude -p "주간 분석 프롬프트 + 컨텍스트" --output-format json
3. 응답에서 JSON 블록 추출 (```json 펜스 또는 최외곽 {} 탐색)
4. JSON 파싱 실패 시: 에러 로깅 + Telegram 알림 + 해당 주 스킵
5. 파싱 성공 → 카테고리별 코드 변경 생성
6. branch 생성 → pytest → 통과 시 자동 머지
```

### Claude Code CLI 프롬프트 구조

```
[시스템] 너는 kindshot 트레이딩 시스템 개선 분석가다.
[컨텍스트] 주간 리포트 요약, 현재 임계값, 승률, 놓친 기회
[지시] 아래 카테고리별로 개선안을 JSON으로 제안하라:

자동 적용 카테고리 (CI 통과 시 머지):
  - threshold_changes: [{param, current, proposed, reason}]
  - prompt_changes: [{file, description, diff}]

리포트 전용 카테고리 (Telegram 알림만, 운영자가 수동 검토):
  - new_checks: [{name, logic, reason}]
  - feature_suggestions: [{name, data_source, reason}]
```

`new_checks`와 `feature_suggestions`는 논리적 정합성을 pytest만으로 검증할 수 없으므로, 자동 적용하지 않고 리포트로만 전달한다.

### 변경 적용 흐름

```
개선 제안 수신
  → 자동 적용 카테고리만 처리:
    threshold: config.py 수정
    prompt: 프롬프트 파일 수정
  → new_checks/feature_suggestions: Telegram 리포트만 전송
  → git checkout -b codex/weekly-YYYY-MM-DD
  → 변경 적용 + commit
  → pytest -x -q 실행
    ├─ 통과 → main에 머지 + Telegram 리포트
    └─ 실패 → branch 유지 + Telegram에 실패 알림 (운영자 개입)
```

### 안전장치

- pytest 실패 시 절대 머지하지 않음
- 임계값 변경 폭 제한: 현재 값 대비 ±30% 이내 (`codex_threshold_max_change_pct`)
- 파라미터별 하드 리밋:
  - `adv_threshold`: 1B ~ 20B won
  - `spread_bps_limit`: 10 ~ 50 bps
  - `extreme_move_pct`: 10% ~ 30%
  - `min_intraday_value_vs_adv20d`: 0.005 ~ 0.05
- 한 주에 변경 가능한 파일 수 상한: 5개 (`codex_weekly_max_files`)
- `new_checks`, `feature_suggestions`는 자동 적용하지 않음 (리포트만)
- 모든 변경은 `logs/codex_loop/weekly/YYYY-MM-DD.json`에 기록

## Telegram 알림 & 거부 메커니즘

### 알림 타이밍

| 이벤트 | 알림 내용 |
|--------|-----------|
| 일일 배치 완료 | 키워드 패치 후보 + 일일 리포트 요약 |
| 주간 심층 완료 | 개선 제안 목록 + 적용 결과 (머지/실패) |
| pytest 실패 | 실패 로그 + branch 이름 |

### 키워드 패치 거부 흐름

**2단계 실행:**

1단계 (D일 16:00): 후보 선정 + 알림
```
daily cron 실행
  → keyword_patch 플러그인이 후보 선정
  → logs/codex_loop/pending_patches.jsonl에 후보 기록
    {"batch_id": "2026-03-18", "keyword": "대규모수주", "bucket": "POS_STRONG",
     "occurrences": 5, "avg_confidence": 92, "status": "pending"}
  → Telegram 알림:
    "키워드 패치 후보:
     POS_STRONG: 대규모수주 (5회, conf 92)
     NEG_STRONG: 감자결정 (3회, conf 88)
     거부하려면 다음 장 마감 전에 /reject keyword 대규모수주 전송"
```

2단계 (D+1일 16:00): 거부 확인 + 적용
```
다음 daily cron 실행
  → pending_patches.jsonl에서 이전 배치 로드
  → 거부된 키워드 제외 (rejected_keywords.jsonl 확인)
  → 남은 후보 → bucket.py 패치 적용
  → pending_patches.jsonl에서 해당 배치 status를 "applied" 또는 "rejected"로 갱신
```

**금요일 후보의 경우:** `codex_reject_window_hours: 24`이지만 주말에는 cron이 실행되지 않으므로, 월요일 16:00에 적용된다. 실질적으로 주말 동안 거부 가능.

- 거부 명령은 기존 `telegram_ops.py`의 명령 처리 구조 활용
- 거부된 키워드는 `logs/codex_loop/rejected_keywords.jsonl`에 기록
  - TTL 기반 만료: 기본 90일 후 재제안 가능 (`codex_keyword_reject_ttl_days`)
- 주간 심층은 거부 없이 CI(pytest) 통과 여부로만 판단

## 설정

### config.py 추가 항목

```python
# Codex Loop
codex_daily_enabled: bool = True
codex_weekly_enabled: bool = True
codex_keyword_min_occurrences: int = 3
codex_keyword_min_confidence: int = 85
codex_threshold_max_change_pct: int = 30
codex_weekly_max_files: int = 5
codex_reject_window_hours: int = 24
codex_keyword_reject_ttl_days: int = 90
```

### cron 스케줄

```cron
# 일일 (장 마감 후 16:00 KST)
0 16 * * 1-5  cd /opt/kindshot && python -m kindshot.codex_loop.engine --schedule daily

# 주간 (토요일 10:00 KST)
0 10 * * 6    cd /opt/kindshot && python -m kindshot.codex_loop.engine --schedule weekly
```

### CLI 진입점

```
python -m kindshot.codex_loop.engine --schedule daily|weekly [--dry-run]
```

`--dry-run`: 변경 생성하되 commit/머지하지 않음. 리포트만 출력.

## 서비스 재시작과 동시성

- daily cron(16:00)은 장 마감 후 실행되므로, main.py 서비스와 동시 실행될 수 있다
- bucket.py 패치 후 Python import cache로 인해 실행 중인 프로세스에는 즉시 반영되지 않음
- 패치 적용 후 서비스 자동 재시작: `systemctl restart kindshot` (deploy 스크립트와 동일 방식)
- 주간 심층(토요일 10:00)은 서비스 미실행 시간이므로 동시성 문제 없음

## 로그 구조 요약

```
logs/codex_loop/
  ├─ keyword_patches.jsonl      # 키워드 패치 적용 이력
  ├─ pending_patches.jsonl      # 거부 대기 중인 키워드 후보
  ├─ rejected_keywords.jsonl    # 거부된 키워드 목록 (TTL 90일)
  ├─ daily/
  │   └─ YYYY-MM-DD.md          # 일일 통계 리포트 (구조화된 마크다운)
  └─ weekly/
      └─ YYYY-MM-DD.json        # 주간 심층 분석 결과 + 적용 내역
```
