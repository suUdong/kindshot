# Kindshot - KRX News Day-Trading MVP

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
