from __future__ import annotations

import os
import stat
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_DIR = REPO_ROOT / "deploy"


def _deploy_scripts() -> list[Path]:
    return sorted(DEPLOY_DIR.glob("*.sh"))


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _stub_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    _write_stub(
        bin_dir,
        "systemctl",
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"is-active\" ]]; then\n"
        "  echo active\n"
        "  exit 0\n"
        "fi\n"
        "echo \"stub-systemctl $*\"\n",
    )
    _write_stub(
        bin_dir,
        "curl",
        "#!/usr/bin/env bash\n"
        "printf '%s' '{\"status\":\"healthy\",\"mode\":\"paper\",\"daily_pnl\":0,\"position_count\":0,\"events_seen\":3}'\n",
    )
    _write_stub(
        bin_dir,
        "journalctl",
        "#!/usr/bin/env bash\n"
        "echo 'Mar 29 09:00:00 kindshot[1]: heartbeat ok'\n",
    )
    _write_stub(
        bin_dir,
        "sudo",
        "#!/usr/bin/env bash\n"
        "exec \"$@\"\n",
    )
    _write_stub(
        bin_dir,
        "ssh",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            cmd="${2:-}"
            case "$cmd" in
              "echo connected")
                echo connected
                ;;
              *"grep -oP '(?<=ExecStart=).*' /etc/systemd/system/kindshot.service"*)
                echo "/opt/kindshot/.venv/bin/python -m kindshot --paper"
                ;;
              *"systemctl is-active kindshot"*)
                echo active
                ;;
              *"curl -s --connect-timeout 3 http://127.0.0.1:8080/health"*)
                printf '%s' '{"status":"healthy","mode":"paper","daily_pnl":0,"position_count":0,"events_seen":5}'
                ;;
              *"grep -E '^KIS_IS_PAPER='"*)
                echo true
                ;;
              *"grep -E '^MICRO_LIVE_MAX_ORDER_WON='"*)
                echo 1000000
                ;;
              *"grep -E '^TELEGRAM_BOT_TOKEN='"*)
                echo token-set
                ;;
              *"grep -E '^KIS_APP_KEY='"*)
                echo app-key
                ;;
              *"grep -E '^KIS_APP_SECRET='"*)
                echo app-secret
                ;;
              *"grep -E '^KIS_ACCOUNT_NO='"*)
                echo 1234567890
                ;;
              *)
                ;;
            esac
            """
        ),
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return env


def test_deploy_shell_scripts_pass_bash_n():
    for script in _deploy_scripts():
        completed = subprocess.run(
            ["bash", "-n", str(script)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, f"{script}: {completed.stderr}"


def test_logs_help_smoke():
    completed = subprocess.run(
        ["bash", str(DEPLOY_DIR / "logs.sh"), "help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "사용법" in completed.stdout
    assert "poll stats" in completed.stdout


def test_verify_live_local_smoke(tmp_path):
    completed = subprocess.run(
        ["bash", str(DEPLOY_DIR / "verify-live.sh"), "--local"],
        cwd=REPO_ROOT,
        env=_stub_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "검증 통과" in completed.stdout
    assert "events_seen=3" in completed.stdout


def test_go_live_verify_smoke(tmp_path):
    completed = subprocess.run(
        ["bash", str(DEPLOY_DIR / "go-live.sh"), "--verify"],
        cwd=REPO_ROOT,
        env=_stub_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "확인 완료" in completed.stdout
    assert "모드: PAPER" in completed.stdout


def test_status_smoke(tmp_path):
    completed = subprocess.run(
        ["bash", str(DEPLOY_DIR / "status.sh")],
        cwd=REPO_ROOT,
        env=_stub_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "헬스 엔드포인트" in completed.stdout
    assert "오늘 로그 파일 없음" in completed.stdout
