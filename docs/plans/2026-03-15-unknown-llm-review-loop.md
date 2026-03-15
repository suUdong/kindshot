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

### Slice 2: Paper Promotion

- paper runtime에서만 high-confidence 승격
- promotion log 추가
- downstream 결과 연결

### Slice 3: Enrichment

- 기사/공시 본문 또는 추가 텍스트 확보
- `needs_article_body` 케이스 보강

### Slice 4: Review-to-Rule Workflow

- `keyword_candidates`를 일일 리뷰 대상으로 자동 집계
- 사람이 승인한 후보만 버킷 사전에 반영

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
