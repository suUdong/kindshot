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

    # v67: 뉴스 카테고리 분류 및 리포트 섹션
    assert "뉴스 카테고리별 분석" in report
    # 주식소각 → shareholder_return, 임상 3상 → clinical_regulatory
    flat_trade = [t for t in trades if t.event_id == "shadow_evt_flat"][0]
    move_trade = [t for t in trades if t.event_id == "shadow_evt_move"][0]
    assert flat_trade.news_type == "other"  # "주식소각" ≠ "자사주소각"
    assert move_trade.news_type == "clinical_regulatory"  # "임상 3상 승인"


def test_render_telegram_summary_within_limit():
    mod = _load_shadow_analysis_module()
    events = [
        {
            "event_id": f"evt_{i}",
            "ticker": f"00{i:04d}",
            "headline": f"테스트 헤드라인 {i}",
            "bucket": "POS_STRONG",
            "decision_confidence": 80 + i,
            "skip_reason": "LOW_CONFIDENCE",
            "detected_at": "2026-03-27T10:00:00+09:00",
        }
        for i in range(5)
    ]
    snapshots = []
    for i in range(5):
        base_px = 10000.0 + i * 1000
        snapshots.extend([
            {"event_id": f"shadow_evt_{i}", "horizon": "t0", "px": base_px, "ts": "2026-03-27"},
            {"event_id": f"shadow_evt_{i}", "horizon": "t+5m", "px": base_px * 1.025, "ts": "2026-03-27"},
        ])

    trades = mod.build_shadow_trades(events, snapshots, tp_pct=2.0, sl_pct=-1.5)
    tg_text = mod.render_telegram_summary(trades, tp_pct=2.0, sl_pct=-1.5)

    assert len(tg_text) <= 4096, f"Telegram 메시지가 4096자 초과: {len(tg_text)}"
    assert "Shadow 기회비용" in tg_text
    assert "LOW_CONFIDENCE" in tg_text


def test_render_telegram_summary_empty():
    mod = _load_shadow_analysis_module()
    tg_text = mod.render_telegram_summary([], tp_pct=2.0, sl_pct=-1.5)
    assert "데이터 없음" in tg_text
