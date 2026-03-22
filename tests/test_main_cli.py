"""Tests for __main__.py and main.py CLI argument parsing."""

from kindshot.main import _parse_args


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
