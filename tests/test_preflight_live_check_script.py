"""Tests for scripts/preflight_live_check.py operator helper."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "preflight_live_check.py"


def _run_script(env: dict[str, str], *extra_args: str) -> subprocess.CompletedProcess:
    full_env = {
        "PYTHONPATH": str(_REPO / "src"),
        "PATH": "/usr/bin:/bin",
        "HOME": str(_REPO),
    }
    full_env.update(env)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *extra_args],
        env=full_env,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )


@pytest.fixture
def marker_env(tmp_path: Path) -> dict[str, str]:
    marker = tmp_path / "live-confirmed.json"
    marker.write_text(json.dumps({"confirmed_at": datetime.now(timezone.utc).isoformat()}))
    return {
        "LIVE_CONFIRMATION_PATH": str(marker),
        "LIVE_AUTO_REVERT_FLAG_PATH": str(tmp_path / "live-auto-revert.flag"),
    }


def test_paper_mode_exits_zero(marker_env: dict[str, str]) -> None:
    env = {**marker_env, "PAPER_TRADING": "true"}
    result = _run_script(env)
    assert result.returncode == 0, result.stderr
    assert "READY" in result.stdout
    assert "no issues" in result.stdout.lower()


def test_live_with_full_env_exits_zero(marker_env: dict[str, str]) -> None:
    env = {
        **marker_env,
        "PAPER_TRADING": "false",
        "LIVE_TRADING_ENABLED": "true",
        "KIS_APP_KEY": "paper-k",
        "KIS_APP_SECRET": "paper-s",
        "KIS_REAL_APP_KEY": "real-k",
        "KIS_REAL_APP_SECRET": "real-s",
        "KIS_ACCOUNT_NO": "12345678-01",
        "MICRO_LIVE_MAX_ORDER_WON": "1000000",
        "LIVE_AUTO_REVERT_LOSS_PCT": "0.02",
    }
    result = _run_script(env)
    assert result.returncode == 0, f"stderr={result.stderr} stdout={result.stdout}"
    assert "READY" in result.stdout


def test_live_without_opt_in_exits_one(marker_env: dict[str, str]) -> None:
    env = {
        **marker_env,
        "PAPER_TRADING": "false",
        "KIS_REAL_APP_KEY": "real-k",
        "KIS_REAL_APP_SECRET": "real-s",
        "KIS_ACCOUNT_NO": "12345678-01",
    }
    result = _run_script(env)
    assert result.returncode == 1
    assert "BLOCKED" in result.stdout
    assert "LIVE_TRADING_ENABLED" in result.stdout


def test_json_mode_emits_machine_payload(marker_env: dict[str, str]) -> None:
    env = {**marker_env, "PAPER_TRADING": "true"}
    result = _run_script(env, "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["exit_code"] == 0
    assert payload["issues"] == []


def test_missing_real_creds_in_live_mode_exits_one(marker_env: dict[str, str]) -> None:
    env = {
        **marker_env,
        "PAPER_TRADING": "false",
        "LIVE_TRADING_ENABLED": "true",
        "KIS_REAL_APP_KEY": "",
        "KIS_REAL_APP_SECRET": "",
        "KIS_ACCOUNT_NO": "12345678-01",
    }
    result = _run_script(env)
    assert result.returncode == 1
    assert "BLOCKED" in result.stdout
    assert "KIS_REAL_APP_KEY" in result.stdout


def test_warning_only_does_not_block(marker_env: dict[str, str]) -> None:
    # threshold=0 is a WARNING (auto-revert disabled) but not an ERROR
    env = {
        **marker_env,
        "PAPER_TRADING": "false",
        "LIVE_TRADING_ENABLED": "true",
        "KIS_APP_KEY": "paper-k",
        "KIS_APP_SECRET": "paper-s",
        "KIS_REAL_APP_KEY": "real-k",
        "KIS_REAL_APP_SECRET": "real-s",
        "KIS_ACCOUNT_NO": "12345678-01",
        "MICRO_LIVE_MAX_ORDER_WON": "1000000",
        "LIVE_AUTO_REVERT_LOSS_PCT": "0.0",
    }
    result = _run_script(env)
    assert result.returncode == 0
    assert "WARN" in result.stdout or "WARNING" in result.stdout


def test_env_file_flag_merges_into_environ(tmp_path: Path, marker_env: dict[str, str]) -> None:
    env_file = tmp_path / "live.env"
    env_file.write_text("PAPER_TRADING=true\n")
    result = _run_script(marker_env, "--env-file", str(env_file))
    assert result.returncode == 0


def test_missing_env_file_exits_two(marker_env: dict[str, str]) -> None:
    result = _run_script(marker_env, "--env-file", "/no/such/file.env")
    assert result.returncode == 2
    assert "env file not found" in result.stderr
