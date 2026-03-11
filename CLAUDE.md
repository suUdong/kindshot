# Kindshot - KRX News Day-Trading MVP

## Stack
Python 3.11+, asyncio, aiohttp, pykrx, Anthropic SDK, pydantic

## Entry
```bash
python -m kindshot          # live
python -m kindshot --paper  # paper trading
python -m kindshot --dry-run
```

## Project Structure
- `src/kindshot/` - main package
  - `feed.py` - KIND RSS / KIS API polling
  - `bucket.py` - keyword-based headline classification
  - `context_card.py` - pykrx historical + KIS realtime features
  - `quant.py` - ADV/spread/extreme move filters
  - `decision.py` - LLM 1-shot BUY/SKIP engine
  - `guardrails.py` - portfolio-level safety checks
  - `market.py` - KOSPI/KOSDAQ halt monitor
  - `main.py` - asyncio supervisor
- `tests/` - pytest
- `deploy/` - Lightsail deployment

## Test
```bash
python -m pytest -x -q
```

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

## Known Limitations
- Sector guardrail inactive (pykrx has no sector API)
- VKOSPI fetch disabled (KRX blocks AWS IPs)
