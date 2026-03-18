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

## 플러그인 1: 키워드 패치 (keyword_patch.py)

### 입력

- `logs/unknown_review/YYYY-MM-DD.jsonl` — LLM 리뷰 결과 (suggested_bucket, suggested_keywords, confidence)
- 누적 기간: 마지막 패치 이후 ~ 오늘

### 패치 조건

- 동일 키워드 **3회 이상** 등장 (`codex_keyword_min_occurrences`)
- 평균 confidence ≥ **85** (`codex_keyword_min_confidence`)
- bucket.py에 이미 존재하지 않을 것
- 이전에 거부된 키워드(`rejected_keywords.jsonl`)가 아닐 것

### 패치 방식

1. bucket.py 읽기
2. 각 버킷 키워드 리스트에 `# --- auto-patch below ---` 마커 존재
3. 마커 아래에 새 키워드를 가나다 순으로 삽입 (정규식 기반)
4. 파일 쓰기

대상 버킷 제한 없음 — LLM이 제안하는 버킷이면 모두 가능 (POS_STRONG, NEG_STRONG, POS_WEAK, NEG_WEAK, IGNORE).

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
   claude -p "주간 분석 프롬프트 + 컨텍스트"
3. 구조화된 개선 제안 파싱
4. 제안별로 코드 변경 생성
5. branch 생성 → pytest → 통과 시 자동 머지
```

### Claude Code CLI 프롬프트 구조

```
[시스템] 너는 kindshot 트레이딩 시스템 개선 분석가다.
[컨텍스트] 주간 리포트 요약, 현재 임계값, 승률, 놓친 기회
[지시] 아래 카테고리별로 개선안을 JSON으로 제안하라:
  - threshold_changes: [{param, current, proposed, reason}]
  - prompt_changes: [{file, description, diff}]
  - new_checks: [{name, logic, reason}]
  - feature_suggestions: [{name, data_source, reason}]
```

### 변경 적용 흐름

```
개선 제안 수신
  → 카테고리별 처리:
    threshold: config.py 수정
    prompt: 프롬프트 파일 수정
    new_check/feature: 코드 파일 생성/수정
  → git checkout -b codex/weekly-YYYY-MM-DD
  → 변경 적용 + commit
  → pytest -x -q 실행
    ├─ 통과 → main에 머지 + Telegram 리포트
    └─ 실패 → branch 유지 + Telegram에 실패 알림 (운영자 개입)
```

### 안전장치

- pytest 실패 시 절대 머지하지 않음
- 임계값 변경 폭 제한: 1회당 ±30% 이내 (`codex_threshold_max_change_pct`)
- 한 주에 변경 가능한 파일 수 상한: 5개 (`codex_weekly_max_files`)
- 모든 변경은 `logs/codex_loop/weekly/YYYY-MM-DD.json`에 기록

## Telegram 알림 & 거부 메커니즘

### 알림 타이밍

| 이벤트 | 알림 내용 |
|--------|-----------|
| 일일 배치 완료 | 키워드 패치 후보 + 일일 리포트 요약 |
| 주간 심층 완료 | 개선 제안 목록 + 적용 결과 (머지/실패) |
| pytest 실패 | 실패 로그 + branch 이름 |

### 키워드 패치 거부 흐름

```
[장 마감 후] 키워드 후보 알림 전송
  "키워드 패치 후보:
   POS_STRONG: 대규모수주 (5회, conf 92)
   NEG_STRONG: 감자결정 (3회, conf 88)
   거부하려면 다음 장 마감 전에 /reject keyword 대규모수주 전송"

[다음 장 마감] 거부되지 않은 키워드 → 자동 패치 적용
```

- 거부 명령은 기존 `telegram_ops.py`의 명령 처리 구조 활용
- 거부된 키워드는 `logs/codex_loop/rejected_keywords.jsonl`에 기록 → 이후 같은 키워드 재제안 방지
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

## 로그 구조 요약

```
logs/codex_loop/
  ├─ keyword_patches.jsonl      # 키워드 패치 이력
  ├─ rejected_keywords.jsonl    # 거부된 키워드 목록
  ├─ daily/
  │   └─ YYYY-MM-DD.md          # 일일 통계 리포트
  └─ weekly/
      └─ YYYY-MM-DD.json        # 주간 심층 분석 결과 + 적용 내역
```
