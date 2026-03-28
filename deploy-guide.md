# Kindshot 배포 가이드

## 서버 정보

| 항목 | 값 |
|------|-----|
| 서버 | AWS Lightsail (3.35.14.35) |
| SSH alias | `kindshot-server` (= `ssh kindshot-server`) |
| 서버 경로 | `/opt/kindshot` |
| Python | 3.11 (venv: `/opt/kindshot/.venv`) |
| systemd | `kindshot.service` (paper mode) |
| 대시보드 | `kindshot-dashboard.service` (Streamlit, 포트 8501) |

## 배포 방법

### 방법 1: rsync (권장)

GitHub 인증 불필요. 로컬 워킹 디렉토리를 직접 서버에 동기화.

```bash
# 1. 코드 전송
rsync -avz \
  --exclude='.venv' --exclude='data/' --exclude='logs/' \
  --exclude='.env' --exclude='__pycache__' --exclude='.git' --exclude='.omc' \
  src/ kindshot-server:/opt/kindshot/src/

rsync -avz --exclude='__pycache__' tests/ kindshot-server:/opt/kindshot/tests/

# (선택) deploy 스크립트, pyproject.toml 업데이트 시
rsync -avz --exclude='__pycache__' deploy/ kindshot-server:/opt/kindshot/deploy/
rsync -avz pyproject.toml kindshot-server:/opt/kindshot/pyproject.toml

# 2. 의존성 설치 + 서비스 재시작
ssh kindshot-server "/opt/kindshot/.venv/bin/python3.11 -m pip install -e /opt/kindshot --quiet && sudo systemctl restart kindshot"

# 3. 상태 확인
ssh kindshot-server "sudo systemctl status kindshot --no-pager"
```

> **주의**: 서버 venv의 pip shebang이 깨져 있어 `pip install` 대신 `python3.11 -m pip install`을 사용해야 함.

### 방법 2: git pull (GitHub 인증 필요)

서버에서 직접 pull.

```bash
ssh kindshot-server "cd /opt/kindshot && git pull && \
  /opt/kindshot/.venv/bin/python3.11 -m pip install -e . --quiet && \
  sudo systemctl restart kindshot"
```

또는 배포 스크립트 사용:
```bash
ssh kindshot-server "cd /opt/kindshot && bash deploy/deploy.sh"
```

> `deploy/deploy.sh`는 `source .venv/bin/activate`를 사용하므로 interactive shell에서만 동작.

### 대시보드 배포

```bash
rsync -avz --exclude='__pycache__' dashboard/ kindshot-server:/opt/kindshot/dashboard/
ssh kindshot-server "sudo systemctl restart kindshot-dashboard"
```

## 배포 후 확인

```bash
# 서비스 상태
ssh kindshot-server "sudo systemctl status kindshot --no-pager"

# 최근 로그
ssh kindshot-server "journalctl -u kindshot -n 20 --no-pager"

# 실시간 로그 모니터링
ssh kindshot-server "journalctl -u kindshot -f"

# 종합 상태 (deploy/status.sh)
ssh kindshot-server "bash /opt/kindshot/deploy/status.sh"
```

## 대시보드 접근 (SSH 터널)

```bash
ssh -L 8501:localhost:8501 kindshot-server
# 브라우저에서 http://localhost:8501 접속
```

## 서버 접속 불가 시 할 수 있는 것

SSH가 안 될 때 로컬에서 할 수 있는 작업:

### 코드 작업 (배포 전까지)
- **테스트**: `pytest -x -q` — 모든 단위/통합 테스트 로컬 실행
- **리플레이 백테스트**: `python -m kindshot --replay data/replay_*.jsonl` — 과거 데이터 재실행
- **코드 리뷰/수정**: 가드레일, 전략 로직, 키워드 튜닝 등 수정 후 커밋
- **config 검토**: `src/kindshot/config.py`에서 파라미터 확인/조정

### 준비 작업
- **git push**: 코드 변경사항을 원격에 푸시 (서버 복구 후 git pull로 배포 가능)
- **deploy 스크립트 수정**: `deploy/` 하위 스크립트 업데이트

### 서버 복구 시 빠른 배포
```bash
# 서버 복구 확인
ssh -o ConnectTimeout=5 kindshot-server "echo OK"

# 밀린 변경 일괄 배포 (rsync 방법 1 실행)
```

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `pip: 명령어를 찾을 수 없음` | non-interactive shell에서 venv activate 실패 | `python3.11 -m pip` 사용 |
| `잘못된 인터프리터` | pip shebang이 `.venv.new` 참조 | `python3.11 -m pip` 사용 |
| 서비스 시작 후 즉시 종료 | `.env` 누락 또는 API 키 미설정 | `.env` 확인 |
| Watchdog 경고 | 장외시간 — feed stale 정상 | 무시 가능 |
| `Market halt` | 장 마감 후 정상 메시지 | 무시 가능 |

## 관련 파일

| 파일 | 용도 |
|------|------|
| `deploy/deploy.sh` | git pull 기반 원커맨드 배포 |
| `deploy/setup.sh` | 서버 초기 구성 (최초 1회) |
| `deploy/status.sh` | 서비스 상태 + 로그 종합 확인 |
| `deploy/go-live.sh` | paper → micro-live 전환 |
| `deploy/kindshot.service` | systemd 서비스 정의 |
| `deploy/run.sh` | 수동 실행 (디버깅용) |
