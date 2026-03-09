# Kindshot Project Memory

## Project
- KRX 뉴스 기반 데이트레이딩 MVP (v0.1.2)
- Stack: Python 3.11+, asyncio, aiohttp, pykrx, Anthropic SDK, pydantic
- Entry: `python -m kindshot` (--dry-run, --paper, --replay)

## Lightsail 배포 (2026-03-09)
- Instance: AWS Lightsail Ubuntu (default 3.10 → 3.11 설치 필요)
- 코드 위치: `/opt/kindshot` (git clone)
- venv: `/opt/kindshot/.venv` (python3.11)
- systemd: `/etc/systemd/system/kindshot.service` (deploy/kindshot.service 복사)
- .env: `/opt/kindshot/.env` (deploy/.env.example 복사 후 키 입력)
- GitHub: https://github.com/suUdong/kindshot.git
- 진행상태: Python 3.11 설치 & pip install 진행 중
- setup.sh가 venv/systemd 자동설정 안 해줌 → 수동으로 진행

## Red Team Audit (2026-03-09)
- Full findings in [red-team-audit.md](red-team-audit.md)
- 25+ issues found across CRITICAL/HIGH/MEDIUM/LOW severity
- Top risks: fail-open guardrails, missing spread/gap data, correction event double-buy, LLM timeout misconfiguration, memory leaks
