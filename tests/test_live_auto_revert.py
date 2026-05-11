"""Tests for LiveAutoRevertGuard — paper-revert hook on intraday loss."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kindshot.broker.auto_revert import LiveAutoRevertGuard
from kindshot.config import Config


def _live_config(tmp_path: Path, *, threshold: float = 0.02) -> Config:
    return Config(
        paper_trading=False,
        live_auto_revert_loss_pct=threshold,
        live_auto_revert_flag_path=str(tmp_path / "live-auto-revert.flag"),
    )


def test_paper_mode_noop() -> None:
    config = Config(paper_trading=True)
    guard = LiveAutoRevertGuard(config)
    assert guard.check(daily_loss_pct=-0.10, equity=1_000_000) is False
    assert not guard.triggered


def test_disabled_threshold_noop(tmp_path: Path) -> None:
    config = _live_config(tmp_path, threshold=0.0)
    guard = LiveAutoRevertGuard(config)
    assert guard.check(daily_loss_pct=-0.50, equity=1_000_000) is False
    assert not guard.triggered


def test_zero_equity_noop(tmp_path: Path) -> None:
    guard = LiveAutoRevertGuard(_live_config(tmp_path))
    assert guard.check(daily_loss_pct=-0.10, equity=0.0) is False


def test_loss_below_threshold_noop(tmp_path: Path) -> None:
    guard = LiveAutoRevertGuard(_live_config(tmp_path, threshold=0.02))
    assert guard.check(daily_loss_pct=-0.015, equity=1_000_000) is False
    assert not guard.triggered


def test_loss_at_threshold_triggers_and_writes_flag(tmp_path: Path) -> None:
    config = _live_config(tmp_path, threshold=0.02)
    guard = LiveAutoRevertGuard(config)
    assert guard.check(daily_loss_pct=-0.02, equity=1_000_000) is True
    assert guard.triggered

    flag_path = Path(config.live_auto_revert_flag_path)
    assert flag_path.exists()
    payload = json.loads(flag_path.read_text())
    assert payload["daily_loss_pct"] == pytest.approx(-0.02)
    assert payload["threshold_pct"] == pytest.approx(0.02)
    assert payload["equity"] == 1_000_000
    assert "triggered_at" in payload
    assert payload["reason"].startswith("live_auto_paper_revert")


def test_idempotent_retrigger_keeps_first_timestamp(tmp_path: Path) -> None:
    guard = LiveAutoRevertGuard(_live_config(tmp_path, threshold=0.02))
    assert guard.check(daily_loss_pct=-0.03, equity=1_000_000) is True
    first_ts = guard.triggered_at
    assert first_ts is not None
    # Second call still True, timestamp unchanged.
    assert guard.check(daily_loss_pct=-0.10, equity=1_000_000) is True
    assert guard.triggered_at == first_ts


def test_oserror_on_flag_write_is_swallowed(tmp_path: Path) -> None:
    guard = LiveAutoRevertGuard(_live_config(tmp_path, threshold=0.02))
    with patch(
        "kindshot.broker.auto_revert.Path.write_text",
        side_effect=OSError("disk full"),
    ):
        # Should not raise — error is logged via logger.exception.
        result = guard.check(daily_loss_pct=-0.05, equity=500_000)
    assert result is True
    assert guard.triggered


def test_reset_clears_in_process_state(tmp_path: Path) -> None:
    guard = LiveAutoRevertGuard(_live_config(tmp_path))
    guard.check(daily_loss_pct=-0.05, equity=1_000_000)
    assert guard.triggered
    guard.reset()
    assert not guard.triggered
    # Flag file intentionally NOT removed by reset()
    assert Path(_live_config(tmp_path).live_auto_revert_flag_path).parent.exists()
