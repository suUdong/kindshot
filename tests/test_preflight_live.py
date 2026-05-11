"""Tests for kindshot.config.preflight_check live-mode gates.

Mirrors crypto-trader b24004b coverage: env opt-in variants, marker
missing/stale/future/malformed, real-creds presence, auto-revert sanity,
and the paper-mode early exit.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kindshot.config import (
    HARD_MAX_DAILY_LOSS_PCT,
    Config,
    preflight_check,
)


def _live_config(
    tmp_path: Path,
    *,
    revert_pct: float = 0.02,
    confirmation_age_hours: float = 24.0,
    real_keys: bool = True,
    micro_max: float = 1_000_000.0,
) -> Config:
    return Config(
        paper_trading=False,
        live_confirmation_path=str(tmp_path / "live-confirmed.json"),
        live_confirmation_max_age_hours=confirmation_age_hours,
        live_auto_revert_loss_pct=revert_pct,
        kis_real_app_key="real-key" if real_keys else "",
        kis_real_app_secret="real-secret" if real_keys else "",
        micro_live_max_order_won=micro_max,
    )


def _write_marker(path: Path, *, age_hours: float = 0.0, now: datetime | None = None,
                  body: dict | str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if body is not None:
        if isinstance(body, dict):
            path.write_text(json.dumps(body))
        else:
            path.write_text(body)
        return
    base = now or datetime.now(timezone.utc)
    ts = (base - timedelta(hours=age_hours)).isoformat()
    path.write_text(json.dumps({"confirmed_at": ts}))


# ---------- paper mode ----------

def test_paper_mode_bypasses_all_gates() -> None:
    config = Config(paper_trading=True)
    assert preflight_check(config, env={}, now=datetime.now(timezone.utc)) == []


# ---------- env opt-in ----------

@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "On"])
def test_env_truthy_variants_accepted(tmp_path: Path, value: str) -> None:
    config = _live_config(tmp_path)
    now = datetime.now(timezone.utc)
    _write_marker(Path(config.live_confirmation_path), now=now)
    issues = preflight_check(
        config,
        env={"LIVE_TRADING_ENABLED": value},
        now=now,
    )
    assert not any(msg.startswith("LIVE_TRADING_ENABLED") for _, msg in issues)


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "maybe"])
def test_env_falsy_variants_blocked(tmp_path: Path, value: str) -> None:
    config = _live_config(tmp_path)
    now = datetime.now(timezone.utc)
    _write_marker(Path(config.live_confirmation_path), now=now)
    issues = preflight_check(
        config,
        env={"LIVE_TRADING_ENABLED": value},
        now=now,
    )
    assert any(lvl == "ERROR" and "LIVE_TRADING_ENABLED" in msg for lvl, msg in issues)


def test_alias_env_var_accepted(tmp_path: Path) -> None:
    config = _live_config(tmp_path)
    now = datetime.now(timezone.utc)
    _write_marker(Path(config.live_confirmation_path), now=now)
    issues = preflight_check(
        config,
        env={"KS_LIVE_TRADING_ENABLED": "1"},
        now=now,
    )
    assert not any(msg.startswith("LIVE_TRADING_ENABLED") for _, msg in issues)


# ---------- confirmation marker ----------

def test_marker_missing_is_error(tmp_path: Path) -> None:
    config = _live_config(tmp_path)
    now = datetime.now(timezone.utc)
    issues = preflight_check(config, env={"LIVE_TRADING_ENABLED": "1"}, now=now)
    assert any(lvl == "ERROR" and "missing" in msg.lower() for lvl, msg in issues)


def test_marker_stale_is_error(tmp_path: Path) -> None:
    config = _live_config(tmp_path, confirmation_age_hours=1.0)
    now = datetime.now(timezone.utc)
    _write_marker(Path(config.live_confirmation_path), age_hours=2.0, now=now)
    issues = preflight_check(config, env={"LIVE_TRADING_ENABLED": "1"}, now=now)
    assert any(lvl == "ERROR" and "stale" in msg.lower() for lvl, msg in issues)


def test_marker_future_dated_is_error(tmp_path: Path) -> None:
    config = _live_config(tmp_path)
    now = datetime.now(timezone.utc)
    future = (now + timedelta(hours=6)).isoformat()
    Path(config.live_confirmation_path).parent.mkdir(parents=True, exist_ok=True)
    Path(config.live_confirmation_path).write_text(json.dumps({"confirmed_at": future}))
    issues = preflight_check(config, env={"LIVE_TRADING_ENABLED": "1"}, now=now)
    assert any(lvl == "ERROR" and "future" in msg.lower() for lvl, msg in issues)


def test_marker_malformed_json_is_error(tmp_path: Path) -> None:
    config = _live_config(tmp_path)
    now = datetime.now(timezone.utc)
    Path(config.live_confirmation_path).parent.mkdir(parents=True, exist_ok=True)
    Path(config.live_confirmation_path).write_text("{not json")
    issues = preflight_check(config, env={"LIVE_TRADING_ENABLED": "1"}, now=now)
    assert any(lvl == "ERROR" and "unreadable" in msg.lower() for lvl, msg in issues)


def test_marker_missing_timestamp_is_error(tmp_path: Path) -> None:
    config = _live_config(tmp_path)
    now = datetime.now(timezone.utc)
    Path(config.live_confirmation_path).parent.mkdir(parents=True, exist_ok=True)
    Path(config.live_confirmation_path).write_text(json.dumps({"foo": "bar"}))
    issues = preflight_check(config, env={"LIVE_TRADING_ENABLED": "1"}, now=now)
    assert any(lvl == "ERROR" and "confirmed_at" in msg for lvl, msg in issues)


# ---------- KIS real creds ----------

def test_missing_real_creds_is_error(tmp_path: Path) -> None:
    config = _live_config(tmp_path, real_keys=False)
    now = datetime.now(timezone.utc)
    _write_marker(Path(config.live_confirmation_path), now=now)
    issues = preflight_check(config, env={"LIVE_TRADING_ENABLED": "1"}, now=now)
    assert any(lvl == "ERROR" and "KIS_REAL_APP_KEY" in msg for lvl, msg in issues)


# ---------- auto-revert sanity ----------

def test_revert_zero_is_warning_only(tmp_path: Path) -> None:
    config = _live_config(tmp_path, revert_pct=0.0)
    now = datetime.now(timezone.utc)
    _write_marker(Path(config.live_confirmation_path), now=now)
    issues = preflight_check(config, env={"LIVE_TRADING_ENABLED": "1"}, now=now)
    levels = [lvl for lvl, msg in issues if "auto_revert" in msg]
    assert "WARNING" in levels
    assert "ERROR" not in levels


def test_revert_above_hard_cap_blocked_at_validate() -> None:
    with pytest.raises(ValueError, match="exceeds HARD_MAX_DAILY_LOSS_PCT"):
        Config(live_auto_revert_loss_pct=HARD_MAX_DAILY_LOSS_PCT + 0.01).validate()


def test_micro_live_zero_in_live_mode_is_error(tmp_path: Path) -> None:
    config = _live_config(tmp_path, micro_max=0.0)
    now = datetime.now(timezone.utc)
    _write_marker(Path(config.live_confirmation_path), now=now)
    issues = preflight_check(config, env={"LIVE_TRADING_ENABLED": "1"}, now=now)
    assert any(lvl == "ERROR" and "micro_live_max_order_won" in msg for lvl, msg in issues)


# ---------- happy path ----------

def test_full_live_preflight_passes_when_all_gates_met(tmp_path: Path) -> None:
    config = _live_config(tmp_path)
    now = datetime.now(timezone.utc)
    _write_marker(Path(config.live_confirmation_path), now=now)
    issues = preflight_check(config, env={"LIVE_TRADING_ENABLED": "1"}, now=now)
    errors = [(lvl, msg) for lvl, msg in issues if lvl == "ERROR"]
    assert errors == [], f"expected no errors, got {errors}"
