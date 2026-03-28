from __future__ import annotations

import runpy
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "test_telegram.py"


def _run_script(monkeypatch, capsys, *args: str) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT_PATH), *args])
    try:
        runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
    except SystemExit as exc:
        code = int(exc.code or 0)
    else:
        code = 0
    return code, capsys.readouterr().out


def test_test_telegram_dry_run_succeeds_without_env(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    code, out = _run_script(monkeypatch, capsys, "--dry-run", "--type", "buy")

    assert code == 0
    assert "[BUY Signal]" in out
    assert "[DRY-RUN] 실제 발송하지 않았습니다." in out


def test_test_telegram_simulate_send_exercises_delivery_path_without_env(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    code, out = _run_script(monkeypatch, capsys, "--simulate-send", "--type", "buy")

    assert code == 0
    assert "발송 결과: OK (simulated)" in out
    assert "simulated_url=https://api.telegram.org/botSIMULATED_BOT_TOKEN/sendMessage" in out
    assert "simulated_chat_id=SIMULATED_CHAT_ID" in out
    assert "[SIMULATED-SEND] 네트워크 없이 send 경로를 검증했습니다." in out
