from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def _load_strategy_comparison_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "strategy_comparison.py"
    spec = spec_from_file_location("strategy_comparison", path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compute_exit_uses_current_stop_loss_default():
    mod = _load_strategy_comparison_module()
    event = {"headline": "일반 공시", "keyword_hits": []}
    snapshots = {
        "t+1m": {"ret_long_vs_t0": -0.016},  # -1.6% < SL -1.5%
        "close": {"ret_long_vs_t0": -0.002},
    }

    exit_type, exit_horizon, exit_ret = mod.compute_exit(event, snapshots)

    assert exit_type == "SL"
    assert exit_horizon == "t+1m"
    assert exit_ret == -1.6


def test_compute_exit_respects_short_hold_profile():
    mod = _load_strategy_comparison_module()
    event = {"headline": "A사 공급계약 체결", "keyword_hits": ["공급계약"]}
    snapshots = {
        "t+5m": {"ret_long_vs_t0": 0.002},
        "t+15m": {"ret_long_vs_t0": 0.003},
        "t+20m": {"ret_long_vs_t0": 0.004},
        "t+30m": {"ret_long_vs_t0": 0.006},
    }

    exit_type, exit_horizon, exit_ret = mod.compute_exit(event, snapshots)

    assert exit_type == "MAX_HOLD"
    assert exit_horizon == "t+30m"
    assert exit_ret == pytest.approx(0.6)
