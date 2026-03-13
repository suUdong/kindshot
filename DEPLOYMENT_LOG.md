# Deployment Log

Kindshot 운영 배포 이력 기록용 문서.

## Rules

- 최신 배포를 문서 최상단에 추가
- 배포 단위마다 날짜, 대상 환경, 커밋/태그, 변경 요약, 검증, 롤백 방법 기록
- 장애/이슈가 있으면 결과와 후속 조치까지 남김

---

## Template

### YYYY-MM-DD HH:MM KST

- Environment:
- Branch:
- Commit:
- Deployer:
- Summary:
- Validation:
- Rollback:
- Result:
- Notes:

---

## Entries

### 2026-03-13 (배포 예정)

- Environment: AWS Lightsail (production, paper mode)
- Branch: `main`
- Commit: `54c3c86`
- Deployer: manual (SSH)
- Summary:
  1. **IGNORE 버킷 신설** — Bucket enum에 IGNORE 추가. 주총/감사보고서 제출/소유주식수 변동/배당락 등 노이즈 사전 필터링
  2. **100+ 키워드 보강** (클로드 리서치 실증 근거):
     - POS_STRONG: 어닝서프라이즈, FDA 승인, 기술수출 계약, 경영권 분쟁, 특별배당 등
     - NEG_STRONG: 어닝쇼크, 적자전환, 물적분할, 임상 실패, 비적정 감사의견, 경영권 분쟁 종료 등
     - POS_WEAK: 인적분할, 매출 증가, 중간배당, 행동주의 주주 등
     - NEG_WEAK: 매출 감소, 최대주주 변경, 임상 지연 등
  3. **버킷 우선순위** — NEG_STRONG > POS_STRONG > NEG_WEAK > POS_WEAK > IGNORE > UNKNOWN
  4. **효과**: 3/12 unknown 758건 → 209건 (72% 감소)
- Validation: `pytest -x -q` 182 passed, 3 skipped
- Rollback: `git revert 54c3c86`
- Result: (배포 후 기록)
- Notes: 리서치 근거 `docs/research/2026-03-13-unknown-bucket-research.md`

---

### 2026-03-12 10:15 KST

- Environment: AWS Lightsail (production, paper mode)
- Branch: `main` (codex/roadmap-loop-foundation merged)
- Commit: `f1d1038` (Harden KIS pipeline) + `5e6ac4b`, `decf7ec` (polling fixes)
- Deployer: manual (SSH)
- Summary:
  1. **KIS 폴링 윈도우 정지 버그 수정** — `last_time` 갱신을 dup check 이전으로 이동. seen_dup만 반복될 때 폴링 윈도우가 전진 안 하던 문제 해결
  2. **KIS news API from_time 제거** — `FID_INPUT_HOUR_1`이 해당 시간 "이후"가 아닌 "이전" 데이터를 반환하는 것으로 확인. 항상 빈 문자열로 최신 뉴스 수신, seen_ids로 중복 제거
  3. **KIS 파이프라인 강화** (codex) — kis_client 리팩터링, guardrails/context_card/decision 개선, 테스트 대폭 추가
  4. **CLAUDE.md/AGENTS.md에 KIS API 레퍼런스 추가** — 공식 예제 레포 및 파라미터 주의사항 문서화
- Validation: `pytest -x -q` 136 passed, 3 skipped
- Rollback: `git revert f1d1038 && git revert decf7ec && git revert 5e6ac4b`
- Result: 배포 진행 중
- Notes: 배포 후 polling_trace에서 `raw_max_time`이 현재 시각 근처로 오는지 확인 필요
