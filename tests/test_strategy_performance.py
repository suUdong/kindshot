from __future__ import annotations

import importlib.util
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "strategy_performance.py"
_SPEC = importlib.util.spec_from_file_location("strategy_performance", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_classify_strategy_labels_pos_weak_as_disabled():
    assert _MODULE.classify_strategy({"bucket": "POS_WEAK", "decision_source": ""}) == "NEWS_WEAK_DISABLED"
