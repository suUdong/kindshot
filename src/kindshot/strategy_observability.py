"""Strategy observability helpers for daily reports and operator summaries."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from kindshot.hold_profile import resolve_hold_profile

_HORIZON_ORDER = ["t+30s", "t+1m", "t+2m", "t+5m", "t+10m", "t+15m", "t+20m", "t+30m", "close"]
_CONTRACT_CANCEL_TERMS = (
    "공급계약 해지",
    "공급계약 해제",
    "공급계약 파기",
    "납품계약 해지",
    "수주계약 해지",
    "계약 해지",
    "계약 해제",
    "계약 파기",
)


@dataclass(frozen=True)
class StrategyReportConfig:
    """Pinned strategy parameters for deterministic report reconstruction."""

    paper_take_profit_pct: float = 1.0
    paper_stop_loss_pct: float = -1.5
    trailing_stop_enabled: bool = True
    trailing_stop_activation_pct: float = 0.3
    trailing_stop_early_pct: float = 0.3
    trailing_stop_mid_pct: float = 0.5
    trailing_stop_late_pct: float = 0.7
    max_hold_minutes: int = 20


def _ret_pct(snapshots: dict[str, dict[str, Any]], horizon: str) -> float | None:
    row = snapshots.get(horizon, {})
    ret = row.get("ret_long_vs_t0")
    if ret is None:
        return None
    return ret * 100


def _trail_pct_for_horizon(horizon: str, config: StrategyReportConfig) -> float:
    if horizon in {"t+30s", "t+1m", "t+2m"}:
        return config.trailing_stop_early_pct
    if horizon in {"t+5m", "t+10m", "t+15m", "t+20m"}:
        return config.trailing_stop_mid_pct
    return config.trailing_stop_late_pct


def classify_buy_exit(
    event: dict[str, Any],
    snapshots: dict[str, dict[str, Any]],
    *,
    config: StrategyReportConfig,
) -> tuple[str | None, str | None]:
    hold_minutes, _matched_keyword = resolve_hold_profile(
        event.get("headline", ""),
        event.get("keyword_hits", []) or [],
        config,
    )
    peak = 0.0
    tp_active = config.paper_take_profit_pct > 0
    sl_active = config.paper_stop_loss_pct < 0
    hold_horizon = f"t+{hold_minutes}m" if hold_minutes > 0 else ""

    for horizon in _HORIZON_ORDER:
        ret_pct = _ret_pct(snapshots, horizon)
        if ret_pct is None:
            continue

        if config.trailing_stop_enabled:
            peak = max(peak, ret_pct)

        if tp_active and ret_pct >= config.paper_take_profit_pct:
            return "take_profit", horizon
        if sl_active and ret_pct <= config.paper_stop_loss_pct:
            return "stop_loss", horizon
        if (
            config.trailing_stop_enabled
            and peak >= config.trailing_stop_activation_pct
            and ret_pct <= peak - _trail_pct_for_horizon(horizon, config)
        ):
            return "trailing_stop", horizon
        if hold_horizon and horizon == hold_horizon:
            return "max_hold", horizon

    return None, None


def _is_contract_cancellation(event: dict[str, Any]) -> bool:
    if event.get("bucket") != "NEG_STRONG":
        return False
    headline = event.get("headline", "")
    keyword_hits = event.get("keyword_hits", []) or []
    haystacks = [headline, *keyword_hits]
    return any(term in text for text in haystacks for term in _CONTRACT_CANCEL_TERMS)


def collect_strategy_summary(
    events: dict[str, dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    snapshots: dict[str, dict[str, dict[str, Any]]],
    config: StrategyReportConfig,
) -> dict[str, Any]:
    """Summarize runtime strategy activity from event/decision/snapshot records."""

    summary = {
        "take_profit_hits": 0,
        "trailing_stop_hits": 0,
        "stop_loss_hits": 0,
        "max_hold_hits": 0,
        "hold_profile_applied": 0,
        "hold_profile_breakdown": Counter(),
        "kill_switch_halts": 0,
        "midday_spread_blocks": 0,
        "market_close_cutoffs": 0,
        "contract_cancellation_negs": 0,
        "skip_tracking_scheduled": len({eid for eid in snapshots if eid.startswith("skip_")}),
    }

    for event in events.values():
        reason = event.get("skip_reason")
        if reason == "CONSECUTIVE_STOP_LOSS":
            summary["kill_switch_halts"] += 1
        elif reason == "MIDDAY_SPREAD_TOO_WIDE":
            summary["midday_spread_blocks"] += 1
        elif reason == "MARKET_CLOSE_CUTOFF":
            summary["market_close_cutoffs"] += 1

        if _is_contract_cancellation(event):
            summary["contract_cancellation_negs"] += 1

    for event_id, decision in decisions.items():
        if decision.get("action") != "BUY":
            continue

        event = events.get(event_id, {})
        hold_minutes, matched_keyword = resolve_hold_profile(
            event.get("headline", ""),
            event.get("keyword_hits", []) or [],
            config,
        )
        if matched_keyword is not None:
            summary["hold_profile_applied"] += 1
            label = "EOD" if hold_minutes == 0 else f"{hold_minutes}m"
            summary["hold_profile_breakdown"][label] += 1

        exit_type, _exit_horizon = classify_buy_exit(event, snapshots.get(event_id, {}), config=config)
        if exit_type == "take_profit":
            summary["take_profit_hits"] += 1
        elif exit_type == "trailing_stop":
            summary["trailing_stop_hits"] += 1
        elif exit_type == "stop_loss":
            summary["stop_loss_hits"] += 1
        elif exit_type == "max_hold":
            summary["max_hold_hits"] += 1

    summary["hold_profile_breakdown"] = dict(summary["hold_profile_breakdown"])
    return summary
