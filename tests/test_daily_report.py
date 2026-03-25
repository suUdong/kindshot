from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_daily_report_module():
    path = Path(__file__).resolve().parents[1] / "deploy" / "daily_report.py"
    spec = spec_from_file_location("daily_report", path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_daily_report_formats_strategy_section():
    mod = _load_daily_report_module()
    log_path = Path("logs/kindshot_20260319.jsonl")
    data = {
        "events": {},
        "decisions": {},
        "snapshots": {},
        "bucket_counts": {"POS_STRONG": 1, "POS_WEAK": 0, "NEG_STRONG": 0, "NEG_WEAK": 0, "IGNORE": 0, "UNKNOWN": 0},
        "hour_dist": {},
        "strategy_summary": {
            "take_profit_hits": 1,
            "trailing_stop_hits": 2,
            "stop_loss_hits": 0,
            "max_hold_hits": 3,
            "hold_profile_applied": 4,
            "hold_profile_breakdown": {"15m": 2, "30m": 2},
            "kill_switch_halts": 0,
            "midday_spread_blocks": 1,
            "market_close_cutoffs": 2,
            "contract_cancellation_negs": 5,
            "skip_tracking_scheduled": 6,
        },
    }

    txt = mod.format_txt(log_path, data)
    telegram = mod.format_telegram(log_path, data)

    assert "전략 동작 현황" in txt
    assert "Trailing Stop 발동: 2회" in txt
    assert "SKIP 추적 스케줄: 6건" in txt
    assert "전략 현황" in telegram
    assert "TS:2 TP:1 SL:0 HoldExit:3" in telegram
