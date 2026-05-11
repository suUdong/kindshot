"""Tests for kindshot.broker.factory.build_broker selection rules."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kindshot.broker.factory import build_broker, select_broker_kind
from kindshot.broker.live import LiveBroker
from kindshot.broker.paper import PaperBroker
from kindshot.config import Config


def _write_marker(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"confirmed_at": datetime.now(timezone.utc).isoformat()}))


def _live_env() -> dict[str, str]:
    return {"LIVE_TRADING_ENABLED": "1"}


def test_paper_trading_returns_paper_broker() -> None:
    config = Config(paper_trading=True)
    broker = build_broker(config, env={})
    assert isinstance(broker, PaperBroker)
    assert broker.name == "paper"


def test_live_with_real_creds_returns_live_broker(tmp_path: Path) -> None:
    marker = tmp_path / "live-confirmed.json"
    _write_marker(marker)
    config = Config(
        paper_trading=False,
        kis_real_app_key="real-key",
        kis_real_app_secret="real-secret",
        kis_app_key="paper-key",
        kis_app_secret="paper-secret",
        kis_account_no="12345678-01",
        live_confirmation_path=str(marker),
    )
    broker = build_broker(config, env=_live_env())
    assert isinstance(broker, LiveBroker)
    assert broker.name == "live"


def test_live_dry_run_returns_dry_live_broker(tmp_path: Path) -> None:
    marker = tmp_path / "live-confirmed.json"
    _write_marker(marker)
    config = Config(
        paper_trading=False,
        kis_real_app_key="real-key",
        kis_real_app_secret="real-secret",
        kis_app_key="paper-key",
        kis_app_secret="paper-secret",
        kis_account_no="12345678-01",
        live_confirmation_path=str(marker),
        live_dry_run=True,
    )
    broker = build_broker(config, env=_live_env())
    assert isinstance(broker, LiveBroker)
    assert broker.dry_run is True
    assert broker.name == "live-dry"


def test_live_without_real_creds_falls_back_to_vts(tmp_path: Path) -> None:
    marker = tmp_path / "live-confirmed.json"
    _write_marker(marker)
    # Preflight ERROR fires on missing real creds → should be blocked, not VTS
    config = Config(
        paper_trading=False,
        kis_real_app_key="",
        kis_real_app_secret="",
        kis_app_key="paper-key",
        kis_app_secret="paper-secret",
        kis_account_no="12345678-01",
        live_confirmation_path=str(marker),
    )
    with pytest.raises(RuntimeError, match="Live preflight failed"):
        build_broker(config, env=_live_env())


def test_preflight_error_blocks_build(tmp_path: Path) -> None:
    # Missing env opt-in even with real creds → preflight blocks
    marker = tmp_path / "live-confirmed.json"
    _write_marker(marker)
    config = Config(
        paper_trading=False,
        kis_real_app_key="real-key",
        kis_real_app_secret="real-secret",
        kis_account_no="12345678-01",
        live_confirmation_path=str(marker),
    )
    with pytest.raises(RuntimeError, match="LIVE_TRADING_ENABLED"):
        build_broker(config, env={})


def test_missing_marker_blocks_build(tmp_path: Path) -> None:
    config = Config(
        paper_trading=False,
        kis_real_app_key="real-key",
        kis_real_app_secret="real-secret",
        kis_account_no="12345678-01",
        live_confirmation_path=str(tmp_path / "missing.json"),
    )
    with pytest.raises(RuntimeError, match="missing"):
        build_broker(config, env=_live_env())


def test_no_creds_in_live_mode_blocks_build(tmp_path: Path) -> None:
    marker = tmp_path / "live-confirmed.json"
    _write_marker(marker)
    config = Config(
        paper_trading=False,
        kis_real_app_key="",
        kis_real_app_secret="",
        kis_app_key="",
        kis_app_secret="",
        kis_account_no="12345678-01",
        live_confirmation_path=str(marker),
    )
    with pytest.raises(RuntimeError, match="Live preflight failed"):
        build_broker(config, env=_live_env())


def test_select_broker_kind_reports_paper() -> None:
    assert select_broker_kind(Config(paper_trading=True), env={}) == "paper"


def test_select_broker_kind_reports_blocked_without_opt_in(tmp_path: Path) -> None:
    marker = tmp_path / "live-confirmed.json"
    _write_marker(marker)
    config = Config(
        paper_trading=False,
        kis_real_app_key="real-key",
        kis_real_app_secret="real-secret",
        kis_account_no="12345678-01",
        live_confirmation_path=str(marker),
    )
    assert select_broker_kind(config, env={}) == "blocked"


def test_select_broker_kind_reports_live(tmp_path: Path) -> None:
    marker = tmp_path / "live-confirmed.json"
    _write_marker(marker)
    config = Config(
        paper_trading=False,
        kis_real_app_key="real-key",
        kis_real_app_secret="real-secret",
        kis_account_no="12345678-01",
        live_confirmation_path=str(marker),
    )
    assert select_broker_kind(config, env=_live_env()) == "live"


def test_select_broker_kind_reports_live_dry(tmp_path: Path) -> None:
    marker = tmp_path / "live-confirmed.json"
    _write_marker(marker)
    config = Config(
        paper_trading=False,
        kis_real_app_key="real-key",
        kis_real_app_secret="real-secret",
        kis_account_no="12345678-01",
        live_confirmation_path=str(marker),
        live_dry_run=True,
    )
    assert select_broker_kind(config, env=_live_env()) == "live-dry"
