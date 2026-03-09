#!/usr/bin/env bash
set -euo pipefail

# kindshot 서버 초기 구성 스크립트
# 사용법: curl ... | bash  또는 ssh로 접속 후 bash setup.sh

APP_DIR="/opt/kindshot"
REPO_URL="https://github.com/suUdong/kindshot.git"

echo "=== 시스템 패키지 설치 ==="
sudo apt-get update -y
sudo apt-get install -y python3.11 python3.11-venv python3-pip git

echo "=== 프로젝트 클론 ==="
sudo mkdir -p "$APP_DIR"
sudo chown "$(whoami):$(whoami)" "$APP_DIR"

if [ -d "$APP_DIR/.git" ]; then
    echo "기존 repo 발견, pull..."
    cd "$APP_DIR" && git pull
else
    git clone "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"

echo "=== venv 생성 및 의존성 설치 ==="
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .

echo "=== 로그 디렉토리 생성 ==="
mkdir -p /opt/kindshot/logs

echo "=== .env 설정 ==="
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/deploy/.env.example" "$APP_DIR/.env"
    echo ">>> .env 파일이 생성되었습니다. API 키를 입력하세요:"
    echo "    nano $APP_DIR/.env"
else
    echo ".env 이미 존재, 스킵"
fi

echo "=== systemd 서비스 등록 ==="
sudo cp "$APP_DIR/deploy/kindshot.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kindshot.service

echo "=== crontab 등록 ==="
bash "$APP_DIR/deploy/cron.sh"

echo ""
echo "=== 완료 ==="
echo "1. .env 편집:  nano $APP_DIR/.env"
echo "2. 수동 테스트: cd $APP_DIR && source .venv/bin/activate && python -m kindshot --dry-run"
echo "3. 서비스 시작: sudo systemctl start kindshot"
echo "4. 로그 확인:   tail -f /opt/kindshot/logs/kindshot_\$(date +%Y%m%d).jsonl"
