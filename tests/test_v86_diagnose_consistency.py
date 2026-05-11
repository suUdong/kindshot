"""scripts/v86_diagnose.py::evaluate 가 VolumeBreakoutFeed._qualifies 와 동일한
판정을 내는지 검증. diagnose 스크립트는 운영용 ground truth 도구이므로 feed 코드와
드리프트 시 진단 결과가 거짓이 된다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from kindshot.config import Config
from kindshot.feeds.volume_breakout_feed import BreakoutSnapshot, VolumeBreakoutFeed

REPO = Path(__file__).resolve().parents[1]


def _load_diagnose_module():
    spec = importlib.util.spec_from_file_location(
        "v86_diagnose", REPO / "scripts" / "v86_diagnose.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["v86_diagnose"] = module
    spec.loader.exec_module(module)
    return module


def _feed_qualifies(cfg: Config, feats: dict[str, Any]) -> bool:
    feed = VolumeBreakoutFeed(cfg)
    snap = BreakoutSnapshot(
        ticker="TEST",
        corp_name="TEST",
        close_today=feats.get("close_today") or 0.0,
        prior_high_n=feats.get("prior_high_n"),
        vol_today=feats.get("vol_today"),
        prior_avg_vol_n=feats.get("prior_avg_vol_n"),
        adv_value_n=feats.get("adv_value_n"),
        ret_today=feats.get("ret_today"),
    )
    return feed._qualifies(snap)


def _qualifying_features() -> dict[str, Any]:
    return {
        "close_today": 12000.0,
        "prior_high_n": 11500.0,
        "vol_today": 3_000_000.0,
        "prior_avg_vol_n": 1_000_000.0,
        "adv_value_n": 5_000_000_000.0,
        "ret_today": 2.5,
    }


@pytest.mark.parametrize(
    "mutator, expected",
    [
        (lambda f: f, True),                                              # baseline qualifies
        (lambda f: {**f, "close_today": 11000.0}, False),                 # no_breakout
        (lambda f: {**f, "vol_today": 1_500_000.0}, False),               # vol_ratio_low
        (lambda f: {**f, "ret_today": 8.5}, False),                       # ret > max
        (lambda f: {**f, "ret_today": -0.5}, False),                      # ret < min
        (lambda f: {**f, "adv_value_n": 100_000_000.0}, False),           # adv too low
        (lambda f: {**f, "prior_high_n": None}, False),                   # missing feature
        (lambda _: {}, False),                                            # no_data
    ],
)
def test_diagnose_evaluate_matches_feed_qualifies(mutator, expected, monkeypatch):
    monkeypatch.setenv("VOLUME_BREAKOUT_FEED_ENABLED", "true")
    monkeypatch.setenv("VOLUME_BREAKOUT_FEED_TICKERS", "005930")
    cfg = Config()

    diagnose = _load_diagnose_module()
    feats = mutator(_qualifying_features())

    diagnose_ok, _, _ = diagnose.evaluate(feats, cfg)
    feed_ok = _feed_qualifies(cfg, feats)

    assert diagnose_ok == feed_ok == expected, (
        f"drift: diagnose={diagnose_ok} feed={feed_ok} expected={expected}"
    )
