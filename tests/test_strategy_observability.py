from kindshot.config import Config
from kindshot.strategy_observability import StrategyReportConfig, collect_strategy_summary


def test_strategy_report_config_defaults_align_with_runtime_config():
    runtime_cfg = Config()
    report_cfg = StrategyReportConfig()

    assert report_cfg.paper_take_profit_pct == runtime_cfg.paper_take_profit_pct
    assert report_cfg.paper_stop_loss_pct == runtime_cfg.paper_stop_loss_pct
    assert report_cfg.trailing_stop_activation_pct == runtime_cfg.trailing_stop_activation_pct
    assert report_cfg.trailing_stop_early_pct == runtime_cfg.trailing_stop_early_pct
    assert report_cfg.trailing_stop_mid_pct == runtime_cfg.trailing_stop_mid_pct
    assert report_cfg.trailing_stop_late_pct == runtime_cfg.trailing_stop_late_pct
    assert report_cfg.max_hold_minutes == runtime_cfg.max_hold_minutes


def test_collect_strategy_summary_counts_key_strategies():
    cfg = StrategyReportConfig()

    events = {
        "buy_tp": {
            "event_id": "buy_tp",
            "headline": "A사 공급계약 체결",
            "keyword_hits": ["공급계약"],
            "bucket": "POS_STRONG",
        },
        "buy_trail": {
            "event_id": "buy_trail",
            "headline": "B사 특허 등록",
            "keyword_hits": ["특허"],
            "bucket": "POS_STRONG",
        },
        "buy_hold": {
            "event_id": "buy_hold",
            "headline": "C사 수주 공시",
            "keyword_hits": ["수주"],
            "bucket": "POS_STRONG",
        },
        "neg_cancel": {
            "event_id": "neg_cancel",
            "headline": "D사 공급계약 해지",
            "keyword_hits": ["공급계약 해지"],
            "bucket": "NEG_STRONG",
            "skip_reason": "NEG_BUCKET",
        },
        "kill_halt": {
            "event_id": "kill_halt",
            "headline": "E사 공급계약 체결",
            "keyword_hits": ["공급계약"],
            "bucket": "POS_STRONG",
            "skip_reason": "CONSECUTIVE_STOP_LOSS",
        },
        "midday": {
            "event_id": "midday",
            "headline": "F사 공급계약 체결",
            "keyword_hits": ["공급계약"],
            "bucket": "POS_STRONG",
            "skip_reason": "MIDDAY_SPREAD_TOO_WIDE",
        },
        "close_cut": {
            "event_id": "close_cut",
            "headline": "G사 공급계약 체결",
            "keyword_hits": ["공급계약"],
            "bucket": "POS_STRONG",
            "skip_reason": "MARKET_CLOSE_CUTOFF",
        },
    }

    decisions = {
        "buy_tp": {"event_id": "buy_tp", "action": "BUY"},
        "buy_trail": {"event_id": "buy_trail", "action": "BUY"},
        "buy_hold": {"event_id": "buy_hold", "action": "BUY"},
    }

    snapshots = {
        "buy_tp": {
            "t+30s": {"ret_long_vs_t0": 0.025},  # v65: TP 2.0% → 2.5%에서 트리거
        },
        "buy_trail": {
            "t+30s": {"ret_long_vs_t0": 0.010},  # v65: activation 0.5% 통과
            "t+1m": {"ret_long_vs_t0": 0.001},    # 하락 → trailing stop
        },
        "buy_hold": {
            "t+15m": {"ret_long_vs_t0": 0.002},
            "t+20m": {"ret_long_vs_t0": 0.003},
            "t+30m": {"ret_long_vs_t0": 0.004},
        },
        "skip_evt1": {"t0": {"ret_long_vs_t0": 0.0}},
        "skip_evt2": {"t0": {"ret_long_vs_t0": 0.0}},
        "skip_evt3": {"t0": {"ret_long_vs_t0": 0.0}},
    }

    summary = collect_strategy_summary(events, decisions, snapshots, cfg)

    assert summary["take_profit_hits"] == 1
    assert summary["trailing_stop_hits"] == 1
    assert summary["max_hold_hits"] == 1
    assert summary["hold_profile_applied"] == 3
    assert summary["hold_profile_breakdown"] == {"30m": 3}
    assert summary["kill_switch_halts"] == 1
    assert summary["midday_spread_blocks"] == 1
    assert summary["market_close_cutoffs"] == 1
    assert summary["contract_cancellation_negs"] == 1
    assert summary["skip_tracking_scheduled"] == 3
