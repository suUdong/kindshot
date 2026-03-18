# Kindshot UNKNOWN 버킷 LLM 보조분류 설계
> 작성: Codex | 2026-03-15

## 목적

현재 Kindshot은 키워드 버킷에 걸리지 않는 공시/기사 헤드라인을 `UNKNOWN`으로 기록하고 종료한다.
이 방식은 보수적이지만, 다음 문제가 남는다.

- `UNKNOWN` 비중이 높으면 실제 유의미한 호재/악재가 누락될 수 있다.
- 수동 키워드 보강 속도가 시장 이벤트 다양성을 따라가기 어렵다.
- 어떤 `UNKNOWN`이 실제로 actionable 했는지 나중에 검증하기 어렵다.

이 문서의 목표는 `UNKNOWN` 이벤트에 대해 LLM 보조분류를 붙이되, 기존 하드 버킷팅을 즉시 대체하지 않고, 검증 가능한 로그와 점진적 롤아웃을 전제로 설계하는 것이다.

---

## 문제 정의

현재 파이프라인:

`feed -> event_registry -> keyword bucket -> quant -> decision -> log`

현재 `UNKNOWN` 처리:

- 헤드라인은 `logs/unknown_headlines/YYYY-MM-DD.jsonl`에 저장
- 실시간 판단은 종료
- 가격 추적도 기본적으로 생략

원하는 목표 상태:

1. `UNKNOWN` 발생 시 별도 저장소에 적재
2. 별도 워커가 LLM에 의미 해석을 요청
3. LLM은 "긍정/부정"만이 아니라 "우리 시스템에 어떤 버킷으로 반영할지"를 구조화해서 반환
4. 결과는 실시간 판단에 반영될 수 있어야 함
5. 모든 보조분류/승격/미승격 판단은 로그로 남겨 사후 검증 가능해야 함

---

## 설계 원칙

### 1. 원본 키워드 버킷은 유지한다

- 1차 분류기는 여전히 deterministic keyword bucket이다.
- LLM 보조분류는 `UNKNOWN`에만 붙는다.
- 기존 `POS_STRONG`, `NEG_STRONG` 등 명확한 경로는 건드리지 않는다.

### 2. LLM은 자동 학습기가 아니라 보조 해석기다

- LLM 출력이 곧바로 키워드 사전 영구 수정으로 이어지면 안 된다.
- 영구 규칙화는 사람이 검토한 뒤 별도 버킷 룰 업데이트로 반영한다.
- 런타임 반영과 룰셋 반영은 분리한다.

### 3. 실시간 반영은 제한적으로만 허용한다

- 모든 `UNKNOWN`을 바로 actionable event로 승격하면 노이즈가 급증할 수 있다.
- 처음에는 shadow mode로 관찰하고, 그 다음 paper에서만 제한 승격한다.
- `live` 런타임 자동 승격은 충분한 검증 이후로 미룬다.

### 4. 로그가 기능보다 먼저다

- 어떤 이벤트가 왜 승격되었는지
- LLM은 무엇을 근거로 그렇게 봤는지
- 승격하지 않은 이유는 무엇인지

위 3가지를 모두 남겨야 나중에 성능/오탐을 점검할 수 있다.

### 5. 기사 본문은 "있으면 사용", 없으면 헤드라인 우선

- 현재 런타임 입력은 헤드라인 중심이다.
- 기사 본문/공시 본문 fetch는 별도 enrichment 계층으로 취급한다.
- 본문 조회 실패가 실시간 파이프라인을 막아서는 안 된다.

---

## 제안 아키텍처

### 런타임 흐름

`feed -> event_registry -> keyword bucket`

여기서 `bucket == UNKNOWN`이면 아래 분기로 간다.

`unknown inbox write -> async review worker -> optional enrich -> LLM review -> review log -> promotion gate -> optional runtime re-entry`

### 구성 요소

1. `UnknownInbox`
- `UNKNOWN` 이벤트를 별도 큐/저장소에 적재
- 최소 저장 필드:
  - `event_id`
  - `ticker`
  - `corp_name`
  - `headline`
  - `rss_link`
  - `detected_at`
  - `source`
  - `original_bucket=UNKNOWN`

2. `UnknownEnricher`
- 가능하면 기사/공시 원문 요약에 필요한 텍스트를 추가 확보
- 후보 소스:
  - KIND 링크 원문
  - KIS 뉴스 본문/상세 API가 있으면 그 결과
- 실패 시 `headline_only=true` 상태로 계속 진행

3. `UnknownReviewEngine`
- LLM에 보조분류 요청
- 출력은 자유 텍스트가 아니라 구조화 JSON
- 목적:
  - 시스템 관점의 버킷 제안
  - 긍정/부정 강도
  - 승격 가능 여부
  - 향후 룰셋 개선용 canonical headline/template 제안

4. `PromotionGate`
- LLM 출력이 있어도 무조건 승격하지 않음
- 설정 가능한 기준:
  - confidence minimum
  - 허용 버킷 목록
  - runtime mode 제한
  - article/body 확보 여부
- 기준 통과 시에만 런타임 판단 경로로 재진입

5. `UnknownReviewLogger`
- inbox 기록
- review 결과
- 승격 여부
- 승격 후 실제 downstream 결과

를 모두 추적

---

## LLM 입력 설계

### 입력 목표

LLM이 단순히 "좋아 보인다/나빠 보인다"를 말하는 것이 아니라, 아래 질문에 답해야 한다.

1. 이 이벤트가 시스템상 actionable 한가
2. actionable 하다면 어느 버킷으로 보내야 하는가
3. 지금 헤드라인만으로 충분한가, 본문이 더 필요했는가
4. 룰 기반 버킷 개선을 위해 어떤 canonical phrase를 추출해야 하는가

### 입력 필드 초안

- `headline`
- `corp_name`
- `ticker`
- `detected_at_kst`
- `source`
- `rss_link`
- `article_text` 또는 `article_excerpt` (있을 때만)
- 선택적 컨텍스트:
  - 최근 가격 급등 여부
  - 스프레드 상태
  - 장중/장후 여부

### 출력 스키마 초안

```json
{
  "suggested_bucket": "POS_STRONG|POS_WEAK|NEG_STRONG|NEG_WEAK|IGNORE|UNKNOWN",
  "polarity": "POSITIVE|NEGATIVE|NEUTRAL|UNCLEAR",
  "confidence": 0,
  "promote_now": false,
  "needs_article_body": false,
  "canonical_headline": "",
  "reason": "",
  "reason_codes": ["CONTRACT", "REGULATORY", "BIO_TRIAL"],
  "keyword_candidates": ["기술수출 계약", "FDA 승인"],
  "risk_flags": ["AMBIGUOUS_COUNTERPARTY", "COMMENTARY_ARTICLE"]
}
```

### 해석 규칙

- `suggested_bucket`
  실시간 파이프라인에 반영 가능한 목적 버킷
- `promote_now`
  지금 런타임 경로에 태워도 되는지에 대한 LLM 의견
- `needs_article_body`
  헤드라인만으로는 불충분했는지 여부
- `canonical_headline`
  사람이 룰셋 개선 시 참고할 표준 표현
- `keyword_candidates`
  향후 deterministic bucket 사전에 편입 검토할 후보

---

## 기사 본문 처리 방향

사용자 요구사항에는 "기사내용까지 확인해서"가 포함되어 있다. 이 요구는 유효하지만, 본문 확보는 현재 파이프라인에서 가장 불확실한 부분이다.

따라서 설계는 2단계로 간다.

### 단계 A: Headline-first

- 헤드라인만으로 review 수행
- 본문이 없어도 작동
- `needs_article_body`를 강제로 출력하게 해서 불확실성을 기록

### 단계 B: Optional article enrichment

- KIND/KIS/기타 소스에서 본문 또는 추가 텍스트 확보
- 확보 성공 시 enriched review 재실행 가능
- 실패 시 헤드라인 기반 결과 유지

핵심은 "본문 fetch 실패 때문에 런타임이 막히지 않게 한다"는 점이다.

---

## 실시간 반영 전략

### 최종 목표

`UNKNOWN` 이벤트 중 일부를 실시간으로 actionable 버킷에 승격시켜 기존 판단 경로에 태운다.

### 권장 단계적 롤아웃

#### Phase 1: Shadow Review

- 모든 `UNKNOWN`에 대해 LLM review만 수행
- 기존 이벤트 버킷은 그대로 `UNKNOWN`
- 어떤 것도 실시간 승격하지 않음
- 로그만 축적

이 단계의 목적:

- LLM이 어떤 버킷을 제안하는지 분포 파악
- confidence calibration 확인
- 사람이 샘플 검토할 재료 확보

#### Phase 2: Paper-only Promotion

- `kindshot run --mode paper`에서만 승격 허용
- 제한 조건 예시:
  - `confidence >= 85`
  - 허용 버킷은 `POS_STRONG`, `NEG_STRONG`만
  - `needs_article_body == false` 또는 body 확보 성공
- 승격된 이벤트는 원본 event와 별도의 review record를 모두 남김

이 단계의 목적:

- 실시간 승격이 paper 성과/오탐에 미치는 영향 관찰
- UNKNOWN 승격 후 quant/guardrail 통과율 측정

#### Phase 3: Controlled Runtime Use

- `live` 자동 승격은 별도 승인 전까지 기본 비활성
- 실제 적용 시에도 kill switch와 최소 confidence threshold 필요

---

## 런타임 재진입 방식

실시간 반영 방법은 두 가지가 있다.

### 옵션 A: In-place override

- 기존 `UNKNOWN` event를 같은 이벤트 안에서 새 버킷으로 덮어씀

장점:
- 구현이 단순함

단점:
- 원본 버킷과 승격 버킷 추적이 헷갈릴 수 있음
- 감사 가능성이 떨어짐

### 옵션 B: Derived promotion event

- 원본 event는 `UNKNOWN`으로 그대로 로그
- 별도 `unknown_review` record를 기록
- 승격 조건 충족 시 `promoted_event` 또는 annotation을 가진 재진입 이벤트 생성

장점:
- 원본/승격 경로를 분리해 감사 가능
- shadow/promotion 결과 비교가 쉬움

단점:
- 이벤트 모델이 조금 복잡해짐

### 권장안

옵션 B를 권장한다.

이유:

- Kindshot의 핵심 리스크는 "보이지 않게 판단 기준이 바뀌는 것"이다.
- UNKNOWN 승격은 버킷팅 규칙을 런타임에서 동적으로 보정하는 행위이므로, 반드시 원본과 파생 결과를 분리해 남겨야 한다.

---

## 로그 및 저장소 설계

### 1. Unknown Inbox 로그

파일 예시:

- `logs/unknown_inbox/YYYY-MM-DD.jsonl`

레코드 예시:

```json
{
  "type": "unknown_inbox",
  "event_id": "evt_123",
  "detected_at": "2026-03-15T09:03:10+09:00",
  "ticker": "005930",
  "corp_name": "삼성전자",
  "headline": "삼성전자, 신규 AI 반도체 협력 확대",
  "rss_link": "https://...",
  "original_bucket": "UNKNOWN",
  "runtime_mode": "paper"
}
```

### 2. Unknown Review 로그

파일 예시:

- `logs/unknown_review/YYYY-MM-DD.jsonl`

레코드 예시:

```json
{
  "type": "unknown_review",
  "event_id": "evt_123",
  "reviewed_at": "2026-03-15T09:03:12+09:00",
  "runtime_mode": "paper",
  "headline_only": true,
  "suggested_bucket": "POS_STRONG",
  "confidence": 88,
  "promote_now": true,
  "needs_article_body": false,
  "canonical_headline": "AI 반도체 대형 협력 확대",
  "reason": "신규 매출/수주 기대를 반영하는 초기 호재 성격",
  "reason_codes": ["NEW_PARTNERSHIP", "REVENUE_EXPECTATION"],
  "keyword_candidates": ["협력 확대", "AI 반도체 협력"],
  "risk_flags": []
}
```

### 3. Promotion 로그

파일 예시:

- `logs/unknown_promotion/YYYY-MM-DD.jsonl`

레코드 예시:

```json
{
  "type": "unknown_promotion",
  "event_id": "evt_123",
  "promoted_at": "2026-03-15T09:03:12+09:00",
  "runtime_mode": "paper",
  "original_bucket": "UNKNOWN",
  "promoted_bucket": "POS_STRONG",
  "confidence": 88,
  "promotion_reason": "LLM_UNKNOWN_REVIEW",
  "promotion_policy": "paper_only_conf85"
}
```

### 저장소 역할 분리

- `unknown_inbox`: 원본 사실 기록
- `unknown_review`: LLM 해석 기록
- `unknown_promotion`: 런타임 반영 기록
- `unknown_headlines`: 기존 수동 키워드 검토용 raw 저장소

기존 `unknown_headlines`는 유지하되, 앞으로는 review 로그와 연결해서 봐야 한다.

---

## 검증 및 분석 항목

### 운영 지표

- `UNKNOWN` 발생 수
- review 성공률
- body enrichment 성공률
- suggested bucket 분포
- promotion rate
- promoted event의 quant 통과율
- promoted event의 BUY/SKIP 분포
- promoted event의 후행 성과

### 품질 지표

- 수동 라벨 대비 suggested bucket precision
- promoted event false positive rate
- `needs_article_body=true` 비율
- keyword_candidates의 실제 룰 편입률

### 샘플 리뷰 방식

- 매일 promoted event 전수 검토
- non-promoted high-confidence sample 일부 검토
- `POS_STRONG`으로 제안됐지만 사람이 보기엔 노이즈인 사례 별도 태깅

---

## 리스크와 대응

### 1. LLM 오탐으로 UNKNOWN 노이즈가 actionable 경로에 들어갈 수 있음

대응:

- 초기에는 shadow mode
- 이후에도 paper-only promotion부터 시작
- high confidence threshold 필요

### 2. 기사 본문 fetch가 느리거나 불안정할 수 있음

대응:

- headline-first 설계
- enrichment는 비동기/옵셔널
- 본문 실패는 로그만 남기고 런타임 중단 금지

### 3. 같은 이벤트를 두 번 처리할 수 있음

대응:

- 원본 event와 promotion event의 관계를 명시
- event_id + review attempt id 기반 dedup 필요

### 4. 키워드 룰보다 LLM이 앞서가며 시스템 설명 가능성이 떨어질 수 있음

대응:

- keyword_candidates와 canonical_headline을 반드시 저장
- 좋은 사례를 주기적으로 deterministic 룰셋으로 환원

---

## 구현 순서 제안

### Slice 1: Shadow Review Only

- `UNKNOWN` inbox 저장
- LLM review 결과 저장
- 승격 없음
- 1차 구현은 보수적으로 opt-in 설정으로 둔다:
  - `UNKNOWN_SHADOW_REVIEW_ENABLED=true`일 때만 worker 활성화
  - `ANTHROPIC_API_KEY`가 없으면 inbox만 기록하고 review는 skip
- 런타임 contract:
  - `UNKNOWN` event는 기존처럼 `event` 로그상 `bucket=UNKNOWN`으로 종료
  - 추가로 `logs/unknown_inbox/YYYY-MM-DD.jsonl`에 원본 inbox를 기록
  - shadow review worker가 가능하면 `logs/unknown_review/YYYY-MM-DD.jsonl`에 구조화 review를 남긴다
  - shadow slice에서는 `unknown_promotion`과 runtime re-entry는 수행하지 않는다
- worker lifecycle:
  - main supervisor가 bounded queue와 background task를 하나 띄운다
  - pipeline worker는 `UNKNOWN`을 만나면 inbox write 후 review queue에 enqueue만 한다
  - shutdown 시 review queue도 drain 후 종료한다
- failure policy:
  - inbox write 실패는 로깅 실패와 동일하게 fail-stop까지는 올리지 않고 warning으로 남긴다
  - review LLM 실패는 `review_status=ERROR`와 `error` 필드를 가진 review log로 남기고 런타임 판단 경로에는 영향 주지 않는다
- 최소 review output contract:
  - `type`, `event_id`, `runtime_mode`, `reviewed_at`, `headline_only`
  - `review_status=OK|ERROR|SKIPPED`
  - `suggested_bucket`, `polarity`, `confidence`, `promote_now`, `needs_article_body`
  - `canonical_headline`, `reason`, `reason_codes`, `keyword_candidates`, `risk_flags`
  - `error`

### Slice 2: Paper Promotion

- paper runtime에서만 high-confidence 승격
- promotion log 추가
- downstream 결과 연결
- 2차 구현도 opt-in 설정으로 둔다:
  - `UNKNOWN_PAPER_PROMOTION_ENABLED=true`일 때만 승격 gate를 연다
  - shadow review가 켜져 있어도 promotion flag가 꺼져 있으면 review 로그까지만 남긴다
- promotion gate contract:
  - `runtime_mode == "paper"`
  - `review_status == OK`
  - `promote_now == true`
  - `confidence >= UNKNOWN_PROMOTION_MIN_CONFIDENCE` 기본값 `85`
  - `needs_article_body == false`
  - 허용 버킷은 1차에서 `POS_STRONG`, `NEG_STRONG`만 허용
- audit/logging contract:
  - `logs/unknown_promotion/YYYY-MM-DD.jsonl`에 gate 결과를 남긴다
  - 최소 필드:
    - `type`, `event_id`, `derived_event_id`, `promoted_at`, `runtime_mode`
    - `review_status`, `original_bucket`, `suggested_bucket`, `confidence`
    - `promotion_status=REJECTED|PROMOTED|ERROR`
    - `promotion_policy`, `gate_reasons`
    - `decision_action`, `skip_stage`, `skip_reason`
  - gate reject도 promotion 로그에 남겨서 승격 불가 사유를 review 로그와 분리해 본다
- runtime re-entry contract:
  - gate 통과 시 원본 `UNKNOWN` event는 그대로 유지한다
  - 별도 derived event를 `event`/`decision` 로그에 기록한다
  - derived event는 `promotion_original_event_id`, `promotion_original_bucket`, `promotion_confidence`, `promotion_policy`를 포함한다
  - `POS_STRONG` 승격은 기존 POS_STRONG 경로와 같은 quant/guardrail/decision path를 탄다
  - `NEG_STRONG` 승격은 기존 NEG_STRONG 경로와 같은 skip+price tracking path를 탄다
  - runtime context-card artifact도 승격 metadata를 같이 적재한다
- failure policy:
  - promotion log write 실패는 review worker warning으로 남기고 런타임 전체를 fail-stop 시키지 않는다
  - derived event 처리 실패는 `promotion_status=ERROR`와 `gate_reasons=["PROMOTION_EXECUTION_ERROR"]`로 남긴다
  - 승격 path의 downstream skip/decision 결과는 promotion log에 summary로 남긴다
- validation:
  - promotion gate allow/reject unit tests
  - paper-mode promoted POS_STRONG event/decision emission test
  - paper-mode promoted NEG_STRONG price tracking test
  - context-card runtime artifact metadata propagation test

### Slice 3: Enrichment

- 기사/공시 본문 또는 추가 텍스트 확보
- `needs_article_body` 케이스 보강

### Slice 3.5: Review Ops Summary

- 운영자가 ad hoc JSONL grep 없이 `UNKNOWN` review/promotion 상태를 바로 볼 수 있는 summary/report 경로를 추가한다
- 기본 경로:
  - `python -m kindshot --unknown-review-summary`
  - optional `--unknown-review-limit N`
  - optional `--unknown-review-out PATH`
- 입력 소스:
  - `logs/unknown_inbox/YYYY-MM-DD.jsonl`
  - `logs/unknown_review/YYYY-MM-DD.jsonl`
  - `logs/unknown_promotion/YYYY-MM-DD.jsonl`
- report contract:
  - multi-day summary first
  - latest day rows with per-day aggregates
  - machine-readable JSON artifact persisted by default
- per-day aggregate minimum fields:
  - `date`
  - `inbox_count`
  - `review_count`
  - `review_ok_count`
  - `review_error_count`
  - `review_skipped_count`
  - `promotion_promoted_count`
  - `promotion_rejected_count`
  - `promotion_error_count`
  - `pending_review_count` = inbox - latest review-known event ids
  - `needs_article_body_count`
  - `top_suggested_buckets`
  - `top_gate_reasons`
  - `health`
- health semantics:
  - `healthy`: inbox가 있고 review/promotion이 정상적으로 닫힌 날
  - `review_backlog`: inbox 대비 review 누락이 남은 날
  - `promotion_errors`: promotion error가 있는 날
  - `review_errors`: review error가 있는 날
  - `empty`: 기록이 없는 날
- artifact contract:
  - default path `data/unknown_review/ops/latest.json`
  - stdout human summary와 동일 원천 데이터 사용
- validation:
  - day aggregate helper test
  - multi-day summary test
  - output override test

### Slice 4: Review-to-Rule Workflow

- `keyword_candidates`를 일일 리뷰 대상으로 자동 집계
- 사람이 승인한 후보만 버킷 사전에 반영
- operator-facing CLI/report 경로를 먼저 둔다:
  - `python -m kindshot --unknown-review-rule-report`
  - optional `--unknown-review-rule-limit N`
  - optional `--unknown-review-rule-out PATH`
- 입력 소스:
  - `unknown_review` latest review rows
  - `unknown_promotion` latest promotion rows
- aggregate contract:
  - canonical headline + keyword candidate phrase 중심으로 묶는다
  - 각 후보마다:
    - `candidate`
    - `canonical_headline_examples`
    - `suggested_bucket_counts`
    - `review_ok_count`
    - `promotion_promoted_count`
    - `promotion_rejected_count`
    - `needs_article_body_count`
    - `top_reason_codes`
    - `top_risk_flags`
    - `sample_headlines`
    - `sample_event_ids`
  - date summary에는:
    - `date`
    - `candidate_count`
    - `promoted_candidate_count`
    - `needs_article_body_candidate_count`
    - `top_candidates`
- selection rules:
  - `review_status=OK` rows만 rule 후보로 집계
  - `keyword_candidates`가 있으면 우선 사용하고, 비어 있으면 `canonical_headline`을 fallback candidate로 사용
  - 같은 event의 latest promotion status를 조인해서 promoted/rejected/error counts를 붙인다
- artifact contract:
  - default path `data/unknown_review/rule_report/latest.json`
  - stdout summary와 JSON artifact를 같이 제공
- next action layer:
  - `python -m kindshot --unknown-review-rule-queue`
  - review report 위에서 실제 rule-candidate queue를 만든다
  - selection rules:
    - `review_ok_count >= UNKNOWN_RULE_QUEUE_MIN_REVIEWS`
    - `promotion_promoted_count >= UNKNOWN_RULE_QUEUE_MIN_PROMOTED`
    - `needs_article_body_count == 0` unless explicitly allowed
    - existing deterministic keyword list와 exact match면 queue에서 제외하고 `already_exists`로 남긴다
  - output rows:
    - `candidate`
    - `recommended_bucket`
    - `review_ok_count`
    - `promotion_promoted_count`
    - `needs_article_body_count`
    - `existing_keyword_bucket`
    - `selection_reason`
    - `canonical_headline_examples`
    - `sample_event_ids`
  - artifact path:
    - default `data/unknown_review/rule_queue/latest.json`
- validation:
  - candidate aggregation test
  - promotion join test
  - output override test
  - queue selection/existing-keyword filter test

### Slice 5: Optional Article Enrichment

- `needs_article_body=true`인 review에 한해 선택적 본문 보강을 붙인다.
- 기본 원칙:
  - 헤드라인 리뷰는 항상 먼저 기록한다.
  - 본문 fetch 실패가 review worker를 막으면 안 된다.
  - 본문 확보 성공 시에만 enriched review를 같은 event에 대해 1회 추가로 기록한다.
  - promotion gate는 latest review record를 기준으로 평가한다.
- rollout:
  - shadow review path에만 먼저 붙인다.
  - `paper` promotion semantics는 유지하되, enriched review가 `needs_article_body=false`로 바뀐 경우만 기존 gate를 다시 통과시킬 수 있다.
  - `live` 동작은 그대로 유지한다.
- config/limits:
  - `UNKNOWN_REVIEW_ARTICLE_ENRICHMENT_ENABLED`
  - `UNKNOWN_REVIEW_ARTICLE_TIMEOUT_S`
  - `UNKNOWN_REVIEW_ARTICLE_MAX_CHARS`
- enrichment source order:
  - `rss_link` 직접 fetch
  - 응답이 HTML이면 `<article>`, 주요 본문 container, `og:description`, `meta description` 순으로 추출 시도
  - 위 추출이 모두 실패하면 body unavailable로 기록하고 종료
- review log minimum additions:
  - `review_iteration` (`headline_initial` | `article_enriched`)
  - `body_fetch_status` (`not_requested` | `fetched` | `fetch_error` | `empty`)
  - `body_source`
  - `body_text_chars`
  - `re_reviewed`
- ops/report additions:
  - per-day aggregate에 `article_enriched_review_count`
  - per-day aggregate에 `article_fetch_success_count`
  - rule candidate aggregate에 `article_enriched_review_count`
  - queue selection은 latest review 기준을 유지하되 enrichment 여부를 operator가 보게 한다
- validation:
  - headline-only fallback test
  - article fetch success -> enriched re-review test
  - fetch failure does not block worker test
  - day summary/article-enriched aggregate test
  - promotion gate uses enriched review outcome test
- rollback:
  - enrichment config를 비활성화하면 기존 headline-only review path로 즉시 복귀한다.
  - 관련 코드 롤백은 review record 확장과 enrichment worker 단계만 되돌리면 된다.

### Slice 6: Direct Rule-Ingest Patch Workflow

- `UNKNOWN` review rule queue에서 사람이 바로 검토할 수 있는 deterministic bucket patch draft를 만든다.
- 목표:
  - queue의 `selected` rows만 읽는다.
  - 추천 bucket 기준으로 키워드를 그룹화한다.
  - 기존 keyword exact match는 다시 포함하지 않는다.
  - operator가 `src/kindshot/bucket.py`에 수동 반영할 때 필요한 target list와 sample evidence를 함께 제공한다.
- safety/rollout:
  - 이 slice는 bucket 사전을 자동 수정하지 않는다.
  - 출력은 draft artifact와 stdout summary에 그친다.
  - 사람이 queue/patch artifact를 검토한 뒤 별도 코드 변경으로 반영한다.
- operator-facing CLI:
  - `python -m kindshot --unknown-review-rule-patch`
  - optional `--unknown-review-rule-patch-limit N`
  - optional `--unknown-review-rule-patch-out PATH`
- input contract:
  - 기본 입력은 `data/unknown_review/rule_queue/latest.json`
  - source rows 중 `selection_reason == "selected"`만 patch 대상
- output contract:
  - default artifact path `data/unknown_review/rule_patch/latest.json`
  - top-level fields:
    - `generated_at`
    - `source_queue_path`
    - `candidate_count`
    - `selected_count`
    - `patch_bucket_count`
    - `rows`
    - `bucket_patches`
  - per-row fields:
    - `candidate`
    - `recommended_bucket`
    - `target_keyword_list`
    - `review_ok_count`
    - `promotion_promoted_count`
    - `article_enriched_review_count`
    - `canonical_headline_examples`
    - `sample_event_ids`
  - per-bucket patch fields:
    - `bucket`
    - `target_keyword_list`
    - `keywords`
    - `source_candidates`
- validation:
  - selected queue row -> patch row transform test
  - same bucket keyword dedup test
  - output override test
  - unsupported/non-selected row exclusion test
- rollback:
  - remove the CLI/artifact path and keep queue generation as the terminal operator step.

### Slice 7: Narrow Keyword Adoption From Patch Draft

- patch draft의 전체 후보를 한 번에 넣지 않고, 기존 bucket 의미를 그대로 확장하는 low-risk 표현만 소수 반영한다.
- 이번 slice의 승인 기준:
  - 기존 bucket semantics와 동일해야 한다.
  - 새 해석 규칙이 아니라 KRX/기사 표기 변형 흡수여야 한다.
  - exact phrase 기반이라 false positive surface가 작아야 한다.
- first adoption target:
  - `NEG_STRONG`: `불성실공시법인지정`, `불성실공시법인 지정`
  - `NEG_WEAK`: `최대주주변경`
- explicit non-goals:
  - `협력 확대`, `성장 기대`, `주주환원 추진` 같은 해석 여지가 큰 문구는 아직 넣지 않는다.
  - patch draft 전체를 자동 반영하지 않는다.
- validation:
  - added phrase가 기대 bucket으로 들어가는 test
  - 기존 인접 headline이 과도하게 reclassify되지 않는 regression test
- rollback:
  - 새로 추가한 소수 phrase만 bucket list에서 제거하면 된다.

### Slice 8: Operator/Exchange Noise Filter

- `UNKNOWN` headline 로그에서 반복되는 거래소/ETP notice를 `IGNORE`로 흡수한다.
- 이번 slice의 승인 기준:
  - 거래 판단 가치가 낮은 거래소 공지/경보/ETP notice여야 한다.
  - 개별 종목 펀더멘털 이벤트가 아니라 운영성 notice여야 한다.
  - exact phrase 기반으로 false positive 표면이 작아야 한다.
- first adoption target:
  - `괴리율 초과 발생`
  - `투자유의 안내`
  - `소수계좌 거래집중 종목`
  - `특정계좌(군) 매매관여 과다종목`
  - `단기과열종목`
  - `공매도 과열종목 지정`
- explicit non-goals:
  - `주요공시`처럼 너무 넓은 요약어는 이번 slice에서 넣지 않는다.
  - 긍부정 bucket 의미는 손대지 않는다.
- validation:
  - notice headline ignore test
  - 기존 supply/approval positive headline regression test
- rollback:
  - 새 IGNORE phrase만 제거하면 된다.

### Slice 9: Narrow Report-Summary Filter

- broad summary headline은 정확한 포맷 신호가 있을 때만 `IGNORE`로 흡수한다.
- 이번 slice의 승인 기준:
  - 개별 종목 이벤트가 아니라 기사/리포트 묶음 제목이어야 한다.
  - exact phrase 또는 포맷 신호만 사용한다.
  - 단독 `주요공시`처럼 범위가 넓은 일반어는 제외한다.
- first adoption target:
  - `전일 장마감 후 주요 종목 공시`
  - `[오늘의 주요공시]`
  - `전 거래일(`
- explicit non-goals:
  - `주요공시` 단독 phrase는 아직 넣지 않는다.
  - 종목명 뒤에 붙는 일반 `공시` 단어는 건드리지 않는다.
- validation:
  - summary headline ignore test
  - generic `주요공시` 미채택 regression test
  - 기존 positive event regression test
- rollback:
  - 새 summary phrase만 제거하면 된다.

### Slice 10: Exact Report-List Title Extension

- summary filter를 넓히지 않고, evidence가 있는 exact report-list title만 추가 채택한다.
- 이번 slice의 승인 기준:
  - 개별 종목 이벤트가 아니라 기사/리스트 제목 자체여야 한다.
  - 로그에서 실제 반복이 확인되어야 한다.
  - generic `주요 종목 공시`처럼 더 넓은 phrase는 제외한다.
- first adoption target:
  - `장중 주요 종목 공시`
- explicit non-goals:
  - `장 마감 후 주요 종목 공시`
  - `주요 종목 공시`
- validation:
  - exact title ignore test
  - generic near-match stays unknown test
  - existing positive event regression test
- rollback:
  - 새 exact title phrase만 제거하면 된다.

### Slice 11: Exact Ownership-Change Summary Title Extension

- broad summary filter를 유지한 채, ownership/disclosure roundup exact title 하나만 추가 채택한다.
- 이번 slice의 승인 기준:
  - 개별 종목 이벤트가 아니라 기사/리스트 제목 자체여야 한다.
  - 로그 evidence가 실제로 있어야 한다.
  - `지분 변동` 일반 phrase처럼 더 넓은 표현으로 확장하지 않는다.
- evidence:
  - `2026-03-16` deployed server review에서 `전일자 주요 지분 변동 공시`가 `UNKNOWN`으로 확인됐다.
- first adoption target:
  - `전일자 주요 지분 변동 공시`
- explicit non-goals:
  - `지분 변동 공시`
  - `주요 지분 변동`
  - `최대주주 변경`
- validation:
  - exact title ignore test
  - generic ownership-change phrase stays unknown test
  - existing positive disclosure regression test
- rollback:
  - 새 exact title phrase만 제거하면 된다.

### Slice 12: Exact Market-Wrap Prefix Extension

- analyst/report prefix 전반으로 넓히지 않고, broad market wrap exact prefix 하나만 추가 채택한다.
- 이번 slice의 승인 기준:
  - 개별 종목 리포트가 아니라 시황/증시 브리핑 제목이어야 한다.
  - 로그 evidence가 실제로 있어야 한다.
  - `[아침밥]`, `[클릭 e종목]`처럼 종목 리포트로도 쓰이는 prefix는 제외한다.
- evidence:
  - `2026-03-16` deployed server review에서 `[굿모닝증시]"중동 포화 속 파월의 입·마이크론 실적…코스피 안개속 장세"`가 `UNKNOWN`으로 확인됐다.
- first adoption target:
  - `[굿모닝증시]`
- explicit non-goals:
  - `[아침밥]`
  - `[클릭 e종목]`
  - `증시` 단독 phrase
- validation:
  - exact market-wrap prefix ignore test
  - generic near-match stays unknown test
  - existing positive stock headline regression test
- rollback:
  - 새 exact prefix만 제거하면 된다.

---

## 명시적 비목표

- 이번 설계는 곧바로 live order 실행 품질을 높이겠다는 문서가 아니다.
- 이번 설계는 LLM이 키워드 룰을 자동으로 수정하는 self-modifying system을 허용하지 않는다.
- 이번 설계는 모든 `UNKNOWN`을 실시간 매매 기회로 간주하지 않는다.

---

## 결론

가장 중요한 원칙은 다음 두 가지다.

1. `UNKNOWN` 해소는 필요하지만, 기존 deterministic bucket을 대체하면 안 된다.
2. 실시간 반영을 하더라도 반드시 "원본 UNKNOWN", "LLM 해석", "승격 결과"를 분리해 기록해야 한다.

따라서 권장 시작점은 다음이다.

- 먼저 `UNKNOWN shadow review`를 구축한다.
- 그 다음 `paper-only promotion`으로 제한 승격을 연다.
- 충분한 로그 검증 후에만 더 넓은 runtime 반영을 검토한다.
