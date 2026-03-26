# Weekly Retro: Kindshot (2026-03-16 ~ 2026-03-23)

**날짜:** 2026-03-23
**프로젝트:** Kindshot v0.1.3
**브랜치:** main
**개발자:** 김동우 (solo)

---

## Metrics Summary

| Metric | Value |
|--------|-------|
| Commits to main | 41 |
| Contributors | 1 (김동우) |
| PRs merged | 0 (direct push to main) |
| Total insertions | ~3,000+ |
| Total deletions | ~500+ |
| Net LOC added | ~2,500+ |
| Version range | 0.1.2 → 0.1.3 |
| Active days | 5 (3/18, 3/19, 3/21, 3/22, 3/23) |
| Detected sessions | 7 |

---

## Commit Time Distribution

```
Hour  Commits
 08:    4      ████
 09:    1      █
 10:    2      ██
 12:    6      ██████
 13:    1      █
 14:    8      ████████  ← peak
 15:    3      ███
 16:    5      █████
 17:    5      █████
 18:    2      ██
 21:    1      █
 22:    1      █
```

**패턴:** 오후 집중형 (14~17시 = 21/41 커밋, 51%). 오전 간헐적, 야간 드물게 1~2건.
점심 시간(12시)에 집중 커밋은 장 마감 후 전략 분석 결과물로 보임.

---

## Work Session Detection (45분 gap)

| # | 날짜 | 시간 | 커밋수 | 시간(분) | 분류 |
|---|------|------|--------|---------|------|
| 1 | 3/18 | 12:08~14:28 | 11 | 140 | Deep |
| 2 | 3/18 | 15:59~18:54 | 10 | 175 | Deep |
| 3 | 3/18 | 21:30 | 1 | - | Micro |
| 4 | 3/19 | 11:44~15:10 | 2 | 205 | Deep (sparse) |
| 5 | 3/21~22 | 22:24~13:02 | 8 | overnight | Deep |
| 6 | 3/22 | 15:18~17:40 | 3 | 142 | Deep |
| 7 | 3/23 | 08:32~10:03 | 7 | 91 | Deep |

**Deep sessions:** 6/7 (86%). 평균 150분. 이 프로젝트는 집중적 딥 워크 세션에서 진행되고 있다.

---

## What Shipped

### 전략 튜닝 (가장 많은 노력)
- **v2~v6 전략 반복:** 승률 17% → 43% → 50%
- LLM 프롬프트 강화 (few-shot 추가, 추세 필터)
- Confidence 차별화 (72 최소, 소형주 집중)
- TP/SL 최적화 (TP 1.5%, SL -1.0%)
- Trailing stop + 30분 보유 룰 도입 → PF 1.12

### 파이프라인 안정화
- LLM 재시도 공통화 (`llm_client.py` 추출)
- Exponential backoff (3회, rate limit 감지)
- Silent except → 로깅 추가
- 파이프라인 예외 안전망
- UNKNOWN 리뷰 max_tokens 증가

### 인프라
- Docker 배포 지원 (Dockerfile + docker-compose)
- 헬스체크 HTTP 서버
- CI/CD 파이프라인
- 도메인 에러 계층 (`errors.py`)
- 로그 로테이션
- 프롬프트 외부화 (`prompts/`)
- Config 검증 로직

### 데이터 수집
- Replay 시뮬레이션 스크립트
- Replay 배치 자동화
- 기술적 지표 보강 (RSI-14, MACD)
- ADV 기반 소형주 필터
- UNKNOWN 파이프라인 완성 (배치 리뷰 + 키워드 피드백)
- 테스트 4개 모듈 추가 (27개 테스트)

---

## What Went Well

1. **전략 튜닝 속도:** 5일 만에 6번 반복. 승률 3배 개선 (17%→50%)은 인상적.
2. **LLM 클라이언트 리팩토링:** `llm_client.py` 추출은 코드 품질 개선의 좋은 예. 중복 제거 + 일관된 에러 처리.
3. **인프라 성숙도 점프:** Docker, CI/CD, 헬스체크, 에러 계층이 한 주에 모두 추가됨. 배포 가능한 프로덕션 형태로 진화.
4. **데이터 중심 의사결정:** Replay 시뮬레이션으로 전략 변경의 효과를 사후 검증하는 프로세스가 확립됨.

---

## What Needs Improvement

### 1. 테스트 깨짐 (CRITICAL)
27개 테스트 파일 중 25개가 collection error. 테스트를 추가하면서 동시에 기존 테스트가 깨진 상태로 커밋. `pytest -x -q` before commit 규칙이 지켜지지 않음.

**Action:** 다음 세션 시작 시 테스트 전체 수정. CI에서 테스트 실패 시 merge 차단.

### 2. 커밋 원자성 부족
3/18에 1시간 30분 동안 11개 커밋 — 일부는 직전 커밋의 수정(`fix:` 바로 뒤에 또 `feat:`). 이는 "커밋 후 발견" 패턴으로, 커밋 전 검증이 부족했음을 시사.

**Action:** 커밋 전 `pytest -x -q` 실행을 pre-commit hook으로 강제화.

### 3. 직접 main push
PR 없이 main에 직접 push. 1인 개발에서는 합리적이나, 코드 리뷰 없이 41개 커밋이 들어간 것은 리스크.

**Action:** 당장은 유지하되, 주요 변경(전략 튜닝, 아키텍처 변경)은 브랜치 → self-review → merge 패턴 도입 고려.

### 4. 전략 튜닝의 체계성
v2→v6가 "커밋하고 결과 보고 다시 조정"하는 패턴. A/B 테스트나 체계적 하이퍼파라미터 탐색 없이 직관적 조정.

**Action:** Replay 배치 스크립트를 활용해 파라미터 그리드 서치 자동화 고려.

---

## Session Patterns & Health

```
Work Pattern:  ████░░░░████████████░░░░
               AM        PM           Night

Style:         Deep-dive focused (86% deep sessions)
Avg session:   ~150 min
Risk:          No short breaks visible between sessions
```

**건강 관찰:** 3/18에 12시~21시까지 거의 연속 작업 (3개 세션, 11시간). 번아웃 리스크 주의.

---

## Velocity Trend

```
Week     Commits   Net LOC   Key Theme
3/18 wk     41     +2,500   전략 튜닝 + 인프라 성숙
(prior)     30+    +2,000   초기 파이프라인 구축
```

**속도:** 매우 높은 velocity. 하지만 속도 > 품질 트레이드오프가 발생 중 (깨진 테스트, 원자성 부족).

---

## Next Week Priorities

1. **P0: 테스트 수정** — 25개 collection error 해결. 이것 없이는 안전한 리팩토링 불가.
2. **P1: Live 전환 준비** — Paper 결과가 일관적이면 소액 테스트 시작
3. **P1: 외부 사용자 확보** — 텔레그램 알림 채널에 지인 1~3명 초대
4. **P2: main.py 분리** — 1,194 LOC God Object 해체

---

## One-line Summary

> 전략 승률이 3배 올랐고 인프라가 프로덕션급으로 성숙했지만, 테스트가 깨진 채로 41개 커밋이 main에 직행한 주였다. 이제 속도를 줄이고 품질을 잡아야 할 때.

---

**STATUS: DONE**
