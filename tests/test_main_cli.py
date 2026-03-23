"""Tests for __main__.py and main.py CLI argument parsing and helpers."""

import asyncio
import pytest

from kindshot.config import Config
from kindshot.main import _parse_args, _run_mode, _wait_or_stop


def test_parse_args_default():
    """Default args should parse without error."""
    import sys
    orig = sys.argv
    try:
        sys.argv = ["kindshot"]
        args = _parse_args()
        assert args.replay is None
        assert args.replay_runtime_date is None
        assert args.replay_day is None
        assert args.paper is False
    finally:
        sys.argv = orig


def test_parse_args_paper_mode():
    import sys
    orig = sys.argv
    try:
        sys.argv = ["kindshot", "--paper"]
        args = _parse_args()
        assert args.paper is True
    finally:
        sys.argv = orig


def test_parse_args_replay():
    import sys
    orig = sys.argv
    try:
        sys.argv = ["kindshot", "--replay", "logs/test.jsonl"]
        args = _parse_args()
        assert args.replay == "logs/test.jsonl"
    finally:
        sys.argv = orig


def test_parse_args_replay_day():
    import sys
    orig = sys.argv
    try:
        sys.argv = ["kindshot", "--replay-day", "20260316"]
        args = _parse_args()
        assert args.replay_day == "20260316"
    finally:
        sys.argv = orig


def test_parse_args_unknown_review_summary():
    import sys
    orig = sys.argv
    try:
        sys.argv = ["kindshot", "--unknown-review-summary"]
        args = _parse_args()
        assert args.unknown_review_summary is True
    finally:
        sys.argv = orig


def test_parse_args_replay_ops_cycle():
    import sys
    orig = sys.argv
    try:
        sys.argv = ["kindshot", "--replay-ops-cycle-ready", "--replay-ops-run-limit", "3"]
        args = _parse_args()
        assert args.replay_ops_cycle_ready is True
        assert args.replay_ops_run_limit == 3
    finally:
        sys.argv = orig


def test_run_mode_dry_run():
    cfg = Config(dry_run=True, paper=False)
    assert _run_mode(cfg) == "dry_run"


def test_run_mode_paper():
    cfg = Config(dry_run=False, paper=True)
    assert _run_mode(cfg) == "paper"


def test_run_mode_live():
    cfg = Config(dry_run=False, paper=False)
    assert _run_mode(cfg) == "live"


@pytest.mark.asyncio
async def test_wait_or_stop_times_out():
    """_wait_or_stop returns after timeout when event not set."""
    stop = asyncio.Event()
    await _wait_or_stop(stop, 0.05)
    assert not stop.is_set()


@pytest.mark.asyncio
async def test_wait_or_stop_interrupted_by_event():
    """_wait_or_stop returns immediately when event is set."""
    stop = asyncio.Event()
    stop.set()
    await _wait_or_stop(stop, 10.0)  # would block 10s without event
    assert stop.is_set()
