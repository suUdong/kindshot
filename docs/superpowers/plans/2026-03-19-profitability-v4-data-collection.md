# 수익성 개선 v4 — 데이터 수집량 극대화

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** paper mode에서 더 많은 종목의 가격 추적 데이터를 확보하여 향후 전략 수립 기반 마련

**Architecture:** 4개 독립 변경 — (1) SKIP 종목도 가격 추적, (2) confidence 임계값 완화, (3) IGNORE 키워드 확대, (4) UNKNOWN shadow review 활성화. 변경 1-3은 완전 독립, 변경 4는 3과 순서 의존(IGNORE 확대 후 남은 UNKNOWN만 review).

**Tech Stack:** Python 3, pytest, asyncio

---

## File Map

| 변경 | 파일 | 작업 |
|------|------|------|
| 1 | `src/kindshot/main.py:382-390` | SKIP도 should_track_price=True |
| 1 | `tests/test_pipeline.py` | SKIP 가격 추적 테스트 추가 |
| 2 | `src/kindshot/config.py:71` | min_buy_confidence 65→50 |
| 2 | `tests/test_guardrails.py` | confidence 임계값 테스트 수정 |
| 3 | `src/kindshot/bucket.py:16-85` | IGNORE 키워드 추가 |
| 3 | `tests/test_bucket.py` | 새 IGNORE 키워드 테스트 |
| 4 | `src/kindshot/config.py:116-117` | shadow review + promotion 기본값 True |
| 4 | `tests/test_unknown_review.py` | 설정 활성화 테스트 확인 |

---

### Task 1: SKIP 종목 가격 추적 (반사실 데이터)

**Files:**
- Modify: `src/kindshot/main.py:288,382-390`
- Modify: `tests/test_pipeline.py`

현재 `_execute_bucket_path()`에서 POS_STRONG/POS_WEAK가 quant 통과 후 LLM decision까지 도달하면 `main.py:509`에서 BUY/SKIP 모두 `schedule_t0`를 호출한다. **문제는 quant 실패 시 `should_track_price=False`라서 가격 추적 없이 return하는 경우.** POS_STRONG/POS_WEAK는 quant 실패해도 가격을 추적해야 반사실 데이터가 쌓인다.

- [ ] **Step 1: 테스트 작성 — quant 실패 POS_STRONG도 가격 추적**

`tests/test_pipeline.py`에 다음 테스트 추가:
```python
@pytest.mark.asyncio
async def test_pos_strong_quant_fail_still_tracks_price():
    """POS_STRONG이 quant 실패해도 price snapshot은 스케줄해야 함."""
    # _execute_bucket_path를 bucket=POS_STRONG, quant 실패 조건으로 호출
    # scheduler.schedule_t0가 호출되었는지 확인
    ...
```

기존 test_pipeline.py의 패턴을 따라서 mock 구성.

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_pipeline.py -v -k "quant_fail_still_tracks" -x`
Expected: FAIL (현재 quant 실패 시 should_track_price=False)

- [ ] **Step 3: main.py 수정 — POS 버킷 quant 실패 시에도 가격 추적**

`src/kindshot/main.py` 약 295행 부근, POS_STRONG/POS_WEAK 분기에서 quant 실패 시에도 `should_track_price = True` 설정:

```python
    elif bucket in (Bucket.POS_STRONG, Bucket.POS_WEAK):
        ctx_card, raw_data = await build_context_card(raw.ticker, kis, config=config)
        ctx = ctx_card

        qr = quant_check(...)
        quant_passed = qr.passed
        quant_detail = qr.detail

        if not qr.passed:
            skip_stage = SkipStage.QUANT
            skip_reason = qr.skip_reason
            should_track_price = True  # ← 변경: quant 실패해도 반사실 데이터 수집
            analysis_tag = analysis_tag or qr.analysis_tag
```

기존 코드에서 `should_track_price = qr.should_track_price` → `should_track_price = True`로 변경.

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_pipeline.py -v -k "quant_fail_still_tracks" -x`
Expected: PASS

- [ ] **Step 5: 전체 테스트**

Run: `pytest -x -q`
Expected: 전체 통과

- [ ] **Step 6: 커밋**

```bash
git add src/kindshot/main.py tests/test_pipeline.py
git commit -m "feat: POS 버킷 quant 실패 시에도 가격 추적 (반사실 데이터 수집)"
```

---

### Task 2: confidence 임계값 완화

**Files:**
- Modify: `src/kindshot/config.py:71`
- Modify: `tests/test_guardrails.py`

단순 설정값 변경. `min_buy_confidence` 65 → 50.

- [ ] **Step 1: config.py 수정**

`src/kindshot/config.py:71`:
```python
# Before:
min_buy_confidence: int = field(default_factory=lambda: _env_int("MIN_BUY_CONFIDENCE", 65))
# After:
min_buy_confidence: int = field(default_factory=lambda: _env_int("MIN_BUY_CONFIDENCE", 50))
```

- [ ] **Step 2: 테스트 확인 — 기존 테스트가 65 하드코딩 여부 확인**

Run: `grep -n "65\|min_buy_confidence" tests/test_guardrails.py`

기존 테스트에서 confidence=65를 경계값으로 테스트하는 부분이 있으면 50으로 조정.

- [ ] **Step 3: 전체 테스트**

Run: `pytest -x -q`
Expected: 전체 통과

- [ ] **Step 4: 커밋**

```bash
git add src/kindshot/config.py tests/test_guardrails.py
git commit -m "feat: min_buy_confidence 65→50 (paper 데이터 수집 확대)"
```

---

### Task 3: IGNORE 키워드 확대

**Files:**
- Modify: `src/kindshot/bucket.py:16-85` (IGNORE_KEYWORDS 리스트)
- Modify: `tests/test_bucket.py`

3/19 UNKNOWN 109건 분석 결과 추가 IGNORE 후보:
- `"전환사채권발행결정"` — CB 발행 행정 공시
- `"유상증자결정"` (제3자배정 — 이미 NEG에 "유상증자" 있으므로 주의: 제3자배정은 별도 패턴)
- `"감사보고서 제출"` — 이미 있음 ✓
- `"정관변경"`, `"정관 일부 변경"` — 이사회 권한 변경 등 행정
- `"괴리율 초과 발생"` — 이미 있음 ✓
- `"투자유의 안내"` — 이미 있음 ✓
- `"상장지수증권"` — 이미 있음 ✓
- `"공시지가"` — 이미 있음 ✓

**실제로 추가 필요한 키워드:**
- `"정관변경"`, `"정관 일부 변경"`, `"정관 변경"`
- `"전환사채권발행결정"`, `"전환사채권발행"` (IGNORE_OVERRIDE로 — CB 발행은 NEG_STRONG의 "전환사채" 키워드보다 먼저 걸려야 함 → 아니, CB발행은 희석이므로 NEG가 맞음. IGNORE 아님)
- `"제3자배정"` (유증 변형 — 이미 NEG에 "유상증자"로 커버될 수 있음)
- `"비거주"`, `"재산세"`, `"종부세"` — 부동산/세금 기사
- `"임대소득세"` — 세금 기사
- `"주식양수도 계약"` — 최대주주 변경은 방향 불명, IGNORE

분석 재확인 후 확정할 키워드 목록:

```python
# 추가 IGNORE 키워드
"정관변경", "정관 일부 변경", "정관 변경",
"주식양수도 계약 체결",
```

- [ ] **Step 1: 테스트 작성 — 새 IGNORE 키워드 분류 확인**

`tests/test_bucket.py`에 추가:
```python
@pytest.mark.parametrize("headline,expected_bucket", [
    ("진바이오텍, 정관 바꿔 이사회 권한 강화", Bucket.IGNORE),
    ("A사 정관변경 결정", Bucket.IGNORE),
    ("정관 일부 변경에 관한 건", Bucket.IGNORE),
])
def test_new_ignore_keywords_v4(headline, expected_bucket):
    result = classify(headline)
    assert result.bucket == expected_bucket
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_bucket.py -v -k "new_ignore_keywords_v4" -x`
Expected: FAIL (아직 키워드 미추가)

- [ ] **Step 3: bucket.py에 키워드 추가**

`src/kindshot/bucket.py`의 `IGNORE_KEYWORDS` 리스트에 추가:
```python
    # 정관 변경 (이사회 권한 등 행정)
    "정관변경", "정관 일부 변경", "정관 변경",
    # 주식양수도 (최대주주 변경 — 방향 불명)
    "주식양수도 계약 체결",
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_bucket.py -v -k "new_ignore_keywords_v4" -x`
Expected: PASS

- [ ] **Step 5: 전체 테스트**

Run: `pytest -x -q`
Expected: 전체 통과

- [ ] **Step 6: 커밋**

```bash
git add src/kindshot/bucket.py tests/test_bucket.py
git commit -m "feat: IGNORE 키워드 확대 (정관변경, 주식양수도)"
```

---

### Task 4: UNKNOWN shadow review + promotion 활성화

**Files:**
- Modify: `src/kindshot/config.py:116-117`
- Check: `tests/test_unknown_review.py`

이미 전체 로직이 구현되어 있음. 설정값만 False→True.

- [ ] **Step 1: config.py 수정**

`src/kindshot/config.py:116-117`:
```python
# Before:
unknown_shadow_review_enabled: bool = field(default_factory=lambda: _env_bool("UNKNOWN_SHADOW_REVIEW_ENABLED", False))
unknown_paper_promotion_enabled: bool = field(default_factory=lambda: _env_bool("UNKNOWN_PAPER_PROMOTION_ENABLED", False))
# After:
unknown_shadow_review_enabled: bool = field(default_factory=lambda: _env_bool("UNKNOWN_SHADOW_REVIEW_ENABLED", True))
unknown_paper_promotion_enabled: bool = field(default_factory=lambda: _env_bool("UNKNOWN_PAPER_PROMOTION_ENABLED", True))
```

- [ ] **Step 2: 기존 테스트 확인 — False 가정하는 테스트가 깨지지 않는지**

Run: `grep -n "UNKNOWN_SHADOW_REVIEW\|UNKNOWN_PAPER_PROMOTION\|shadow_review_enabled\|paper_promotion_enabled" tests/test_unknown_review.py tests/test_pipeline.py`

기본값이 True로 바뀌면 기존 테스트에서 명시적으로 False를 넘기는 부분이 필요할 수 있음. 확인 후 수정.

- [ ] **Step 3: 전체 테스트**

Run: `pytest -x -q`
Expected: 전체 통과

- [ ] **Step 4: 커밋**

```bash
git add src/kindshot/config.py tests/test_unknown_review.py
git commit -m "feat: UNKNOWN shadow review + paper promotion 기본 활성화"
```

---

### Task 5: 통합 테스트 & 최종 확인

- [ ] **Step 1: 전체 테스트 스위트 실행**

Run: `pytest -x -q`
Expected: 전체 통과

- [ ] **Step 2: 서버 배포 (rsync)**

```bash
rsync -avz --exclude='.venv' --exclude='data/' --exclude='logs/' --exclude='.env' --exclude='__pycache__' --exclude='.git' src/ kindshot-server:/opt/kindshot/src/
rsync -avz --exclude='__pycache__' tests/ kindshot-server:/opt/kindshot/tests/
ssh kindshot-server "cd /opt/kindshot && source .venv/bin/activate && pip install -e . --quiet && sudo systemctl restart kindshot"
```

- [ ] **Step 3: 배포 확인**

```bash
ssh kindshot-server "sudo systemctl status kindshot --no-pager"
ssh kindshot-server "journalctl -u kindshot -n 20 --no-pager"
```

UNKNOWN shadow review 로그가 출력되는지, POS_WEAK SKIP 종목도 price_snapshot이 기록되는지 확인.
