# Kindshot - KRX News Day-Trading MVP

## Language
- 대화는 한국어로 진행

## Conventions
- Commit: `fix:`, `feat:`, `chore:` prefix. No emoji.
- Korean comments OK in domain logic (bucket keywords, etc.)
- Config defaults in `config.py`, override via env vars

## Workflow

### Always do
1. Read before edit
2. `pytest -x -q` before commit
3. Keep commits atomic and descriptive

### Superpowers usage by task type

**New feature / new module (2+ files):**
1. brainstorming -> requirements & edge cases
2. writing-plans -> implementation steps
3. test-driven-development -> tests first
4. code-review -> before merge

**Bugfix (root cause unclear):**
1. systematic-debugging -> structured investigation
2. verification-before-completion -> confirm fix

**Bugfix (root cause obvious, 1 file):**
- Fix directly, no skills needed

**Config/keyword/threshold change:**
- Fix directly, no skills needed

### Why: pay upfront, save overall
Skipping skills during implementation leads to subtle bugs (null data, wrong defaults,
cache key omissions) that cost more tokens to find and fix later.

## Deploy
Push to main -> SSH to Lightsail -> `cd /opt/kindshot && bash deploy/deploy.sh`

## KIS API Reference
- 공식 예제 레포: https://github.com/koreainvestment/open-trading-api
- LLM용 예제: `examples_llm/domestic_stock/` 하위 API별 폴더
- KIS API 파라미터 동작이 불확실할 때 위 레포의 예제를 반드시 참조할 것
- 주요 주의사항:
  - `FID_INPUT_HOUR_1`: 빈 문자열 = 현재 기준 최신, 값 입력 시 해당 시간 **이전** 데이터 반환
  - `FID_INPUT_DATE_1`: 빈 문자열 = 현재 기준, 포맷 `00YYYYMMDD`
  - 페이지네이션: 응답 헤더 `tr_cont == "M"`이면 다음 페이지 존재

## Known Limitations
- Sector guardrail inactive (pykrx has no sector API)
- VKOSPI fetch disabled (KRX blocks AWS IPs)
