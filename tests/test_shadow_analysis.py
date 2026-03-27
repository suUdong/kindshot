from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def _load_shadow_analysis_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "shadow_analysis.py"
    spec = spec_from_file_location("shadow_analysis", path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_render_report_includes_reason_hour_and_flat_sections():
    mod = _load_shadow_analysis_module()
    events = [
        {
            "event_id": "evt_flat",
            "ticker": "001510",
            "headline": "SK증권(주) 변경상장(주식소각)",
            "bucket": "POS_STRONG",
            "decision_confidence": 78,
            "skip_reason": "MARKET_CLOSE_CUTOFF",
            "detected_at": "2026-03-27T16:55:16+09:00",
        },
        {
            "event_id": "evt_move",
            "ticker": "006280",
            "headline": "GC녹십자, 베트남서 수두백신 임상 3상 승인",
            "bucket": "POS_STRONG",
            "decision_confidence": 86,
            "skip_reason": "LOW_CONFIDENCE",
            "detected_at": "2026-03-27T13:10:00+09:00",
        },
    ]
    snapshots = [
        {"event_id": "shadow_evt_flat", "horizon": "t0", "px": 2070.0, "ts": "2026-03-27T07:55:22Z", "price_source": "KIS_REST"},
        {"event_id": "shadow_evt_flat", "horizon": "t+30s", "px": 2070.0, "ts": "2026-03-27T07:55:52Z", "price_source": "KIS_REST"},
        {"event_id": "shadow_evt_flat", "horizon": "t+1m", "px": 2070.0, "ts": "2026-03-27T07:56:22Z", "price_source": "KIS_REST"},
        {"event_id": "shadow_evt_move", "horizon": "t0", "px": 10000.0, "ts": "2026-03-27T04:10:00Z", "price_source": "KIS_REST"},
        {"event_id": "shadow_evt_move", "horizon": "t+1m", "px": 10300.0, "ts": "2026-03-27T04:11:00Z", "price_source": "KIS_REST"},
    ]

    trades = mod.build_shadow_trades(events, snapshots, tp_pct=2.0, sl_pct=-1.5)
    report = mod.render_report(trades, tp_pct=2.0, sl_pct=-1.5)

    assert len(trades) == 2
    assert any(t.flat_price for t in trades)
    assert "차단 사유별 분석" in report
    assert "시간대별 분석 (KST)" in report
    assert "Flat-price / stale 의심 건" in report
    assert "MARKET_CLOSE_CUTOFF" in report
    assert "16:00" in report
