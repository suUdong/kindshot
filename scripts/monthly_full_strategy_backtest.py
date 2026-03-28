#!/usr/bin/env python3
"""Unified monthly full-strategy backtest report for Kindshot."""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
import tempfile
from calendar import monthrange
from collections import Counter
from contextlib import redirect_stderr
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kindshot.config import Config
from kindshot.decision import DecisionEngine
from kindshot.guardrails import GuardrailState, check_guardrails
from kindshot.hold_profile import get_max_hold_minutes
from kindshot.models import Action, ContextCard
from kindshot.price import _apply_entry_slippage
from kindshot.trade_db import VERSION_MAP, TradeDB, backfill_from_logs, simulate_version_on_trades

from scripts.backtest_analysis import (
    ExitSimulationConfig,
    _append_runtime_snapshots,
    analyze_paths,
    build_trades,
    load_day,
)

VERSION_TAGS = ("v64", "v65", "v66", "v67", "v68", "v69", "v70")


@dataclass(frozen=True)
class CandidateTrade:
    detected_at: datetime
    trade: Any
    event: dict[str, Any]
    decision: dict[str, Any]
    snapshot_rows: dict[str, dict[str, Any]]
    context: ContextCard
    raw: dict[str, Any]
    delay_ms: int | None
    sector: str


@dataclass(frozen=True)
class TransactionCostConfig:
    buy_fee_bps: float = 1.5
    sell_fee_bps: float = 1.5
    sell_tax_bps: float = 20.0
    exit_slippage_half_spread_ratio: float = 0.5

    def to_dict(self) -> dict[str, float]:
        return {
            "buy_fee_bps": self.buy_fee_bps,
            "sell_fee_bps": self.sell_fee_bps,
            "sell_tax_bps": self.sell_tax_bps,
            "exit_slippage_half_spread_ratio": self.exit_slippage_half_spread_ratio,
        }


@dataclass(frozen=True)
class SettledTrade:
    event_id: str
    date: str
    ticker: str
    headline: str
    confidence: int
    size_hint: str
    exit_type: str
    exit_ret_pct: float
    net_ret_pct: float
    pnl_won: float
    net_pnl_won: float
    buy_fee_pct: float
    sell_fee_pct: float
    sell_tax_pct: float
    exit_slippage_pct: float
    total_cost_pct: float
    exit_spread_bps: float | None
    runtime_entry_slippage_aligned: bool
    close_at: str


def _parse_log_date(path: Path) -> datetime:
    return datetime.strptime(path.stem.replace("kindshot_", ""), "%Y%m%d")


def _subtract_months(dt: datetime, months: int) -> datetime:
    year = dt.year
    month = dt.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(dt.day, monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def resolve_requested_window(
    log_dir: Path,
    *,
    lookback_days: int | None = None,
    lookback_months: int | None = None,
) -> tuple[datetime | None, datetime | None]:
    paths = sorted(log_dir.glob("kindshot_*.jsonl"))
    if not paths:
        return None, None
    latest = max(_parse_log_date(path) for path in paths)
    if lookback_months is not None:
        earliest = _subtract_months(latest, lookback_months)
    else:
        earliest = latest - timedelta(days=max((lookback_days or 30) - 1, 0))
    return earliest, latest


def select_log_paths(
    log_dir: Path,
    *,
    lookback_days: int | None = None,
    lookback_months: int | None = None,
) -> list[Path]:
    paths = sorted(log_dir.glob("kindshot_*.jsonl"))
    earliest, latest = resolve_requested_window(
        log_dir,
        lookback_days=lookback_days,
        lookback_months=lookback_months,
    )
    if earliest is None or latest is None:
        return []
    return [path for path in paths if earliest <= _parse_log_date(path) <= latest]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_context_index(context_dir: Path, dates: set[str]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    if not context_dir.exists():
        return index
    for path in sorted(context_dir.glob("*.jsonl")):
        if path.stem not in dates:
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw:
                continue
            row = json.loads(raw)
            event_id = str(row.get("event_id", "")).strip()
            if event_id:
                index[event_id] = row
    return index


def collect_candidate_trades(
    log_paths: list[Path],
    *,
    snapshot_dir: Path,
    context_dir: Path,
    runtime_defaults: ExitSimulationConfig,
) -> list[CandidateTrade]:
    dates = {path.stem.replace("kindshot_", "") for path in log_paths}
    context_index = _load_context_index(context_dir, dates)
    candidates: list[CandidateTrade] = []

    for path in log_paths:
        date_str = path.stem.replace("kindshot_", "")
        events, decisions, snapshots = load_day(path)
        _append_runtime_snapshots(date_str, snapshots, snapshot_dir)
        trades = build_trades(events, decisions, snapshots, date_str, runtime_defaults)
        snapshot_map: dict[str, dict[str, dict[str, Any]]] = {}
        for row in snapshots:
            event_id = str(row.get("event_id", ""))
            horizon = str(row.get("horizon", ""))
            if event_id and horizon:
                snapshot_map.setdefault(event_id, {})[horizon] = row
        event_map = {str(row.get("event_id", "")): row for row in events}
        decision_map = {
            str(row.get("event_id", "")): row
            for row in decisions
            if row.get("action") == "BUY"
        }
        for trade in trades:
            event = event_map.get(trade.event_id, {})
            decision = decision_map.get(trade.event_id, {})
            ctx_payload = dict(event.get("ctx") or {})
            ctx_payload.update({k: v for k, v in (context_index.get(trade.event_id, {}).get("ctx") or {}).items() if v is not None})
            context = ContextCard(**ctx_payload)
            raw = dict(context_index.get(trade.event_id, {}).get("raw") or {})
            sector = str(raw.get("sector", "") or "")
            delay_ms = context_index.get(trade.event_id, {}).get("delay_ms", event.get("delay_ms"))
            detected_at = datetime.fromisoformat(str(event.get("detected_at", "")).replace("Z", "+00:00"))
            candidates.append(
                CandidateTrade(
                    detected_at=detected_at,
                    trade=trade,
                    event=event,
                    decision=decision,
                    snapshot_rows=dict(snapshot_map.get(trade.event_id, {})),
                    context=context,
                    raw=raw,
                    delay_ms=int(delay_ms) if isinstance(delay_ms, int) else None,
                    sector=sector,
                )
            )

    candidates.sort(key=lambda row: row.detected_at)
    return candidates


def _summarize_pnls(
    rows: list[Any],
    *,
    return_attr: str = "exit_ret_pct",
    pnl_attr: str = "pnl_won",
) -> dict[str, Any]:
    if not rows:
        return {
            "trade_count": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "avg_ret_pct": 0.0,
            "total_ret_pct": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "total_pnl_won": 0.0,
        }

    rets = [float(getattr(row, return_attr)) for row in rows]
    wins = [value for value in rets if value > 0]
    losses = [value for value in rets if value <= 0]
    cumulative = 0.0
    peak = 0.0
    mdd = 0.0
    for value in rets:
        cumulative += value
        peak = max(peak, cumulative)
        mdd = min(mdd, cumulative - peak)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss else (math.inf if gross_win > 0 else 0.0)
    return {
        "trade_count": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(rows) * 100, 1),
        "avg_ret_pct": round(sum(rets) / len(rets), 4),
        "total_ret_pct": round(sum(rets), 4),
        "avg_win_pct": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "avg_loss_pct": round(sum(losses) / len(losses), 4) if losses else 0.0,
        "profit_factor": None if not math.isfinite(profit_factor) else round(profit_factor, 2),
        "max_drawdown_pct": round(mdd, 4),
        "total_pnl_won": round(sum(float(getattr(row, pnl_attr)) for row in rows), 0),
    }


def _pct_from_bps(value: float) -> float:
    return value / 100.0


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _build_cost_breakdown(
    gross_ret_pct: float,
    *,
    exit_spread_bps: float | None,
    cost_config: TransactionCostConfig,
) -> dict[str, float | None]:
    buy_fee_pct = _pct_from_bps(cost_config.buy_fee_bps)
    sell_fee_pct = _pct_from_bps(cost_config.sell_fee_bps)
    sell_tax_pct = _pct_from_bps(cost_config.sell_tax_bps)
    exit_slippage_pct = 0.0
    if exit_spread_bps is not None and exit_spread_bps > 0:
        exit_slippage_pct = _pct_from_bps(exit_spread_bps * cost_config.exit_slippage_half_spread_ratio)
    total_cost_pct = buy_fee_pct + sell_fee_pct + sell_tax_pct + exit_slippage_pct
    return {
        "gross_ret_pct": round(gross_ret_pct, 4),
        "net_ret_pct": round(gross_ret_pct - total_cost_pct, 4),
        "buy_fee_pct": round(buy_fee_pct, 4),
        "sell_fee_pct": round(sell_fee_pct, 4),
        "sell_tax_pct": round(sell_tax_pct, 4),
        "exit_slippage_pct": round(exit_slippage_pct, 4),
        "total_cost_pct": round(total_cost_pct, 4),
        "exit_spread_bps": None if exit_spread_bps is None else round(exit_spread_bps, 4),
    }


def _validate_runtime_entry_slippage(snapshot_rows: dict[str, dict[str, Any]], exit_horizon: str | None) -> bool:
    if not exit_horizon:
        return False
    t0_row = snapshot_rows.get("t0") or {}
    exit_row = snapshot_rows.get(exit_horizon) or {}
    t0_px = _float_or_none(t0_row.get("px"))
    exit_px = _float_or_none(exit_row.get("px"))
    spread_bps = _float_or_none(t0_row.get("spread_bps"))
    logged_ret = _float_or_none(exit_row.get("ret_long_vs_t0"))
    if t0_px is None or t0_px <= 0 or exit_px is None or spread_bps is None or logged_ret is None:
        return False
    effective_entry_px = _apply_entry_slippage(t0_px, spread_bps, mode="paper", is_buy_decision=True)
    if effective_entry_px is None or effective_entry_px <= 0:
        return False
    expected_ret = (exit_px - effective_entry_px) / effective_entry_px
    return abs(expected_ret - logged_ret) <= 1e-9


def _summarize_cost_validation(rows: list[SettledTrade]) -> dict[str, Any]:
    return {
        "trade_count": len(rows),
        "runtime_entry_slippage_aligned_count": sum(1 for row in rows if row.runtime_entry_slippage_aligned),
        "exit_spread_available_count": sum(1 for row in rows if row.exit_spread_bps is not None),
        "exit_spread_missing_count": sum(1 for row in rows if row.exit_spread_bps is None),
    }


def simulate_current_strategy(
    candidates: list[CandidateTrade],
    *,
    config: Config,
    cost_config: TransactionCostConfig,
) -> dict[str, Any]:
    if not candidates:
        return {
            "candidate_trade_count": 0,
            "accepted_trade_count": 0,
            "blocked_trade_count": 0,
            "blocked_by_reason": {},
            "summary": _summarize_pnls([]),
            "gross_summary": _summarize_pnls([]),
            "net_summary": _summarize_pnls([], return_attr="net_ret_pct", pnl_attr="net_pnl_won"),
            "cost_validation": _summarize_cost_validation([]),
            "accepted_trades": [],
        }

    engine = DecisionEngine(config)
    blocked_by_reason: Counter[str] = Counter()
    settled: list[SettledTrade] = []
    open_positions: list[dict[str, Any]] = []
    state: GuardrailState | None = None
    current_date: str | None = None

    def settle_until(ts: datetime) -> None:
        nonlocal open_positions, state
        assert state is not None
        pending: list[dict[str, Any]] = []
        for position in sorted(open_positions, key=lambda row: row["close_at"]):
            if position["close_at"] > ts:
                pending.append(position)
                continue
            state.record_sell(position["ticker"], sector=position["sector"])
            cost_breakdown = _build_cost_breakdown(
                position["exit_ret_pct"],
                exit_spread_bps=position["exit_spread_bps"],
                cost_config=cost_config,
            )
            pnl_won = config.order_size_for_hint(position["size_hint"]) * (position["exit_ret_pct"] / 100.0)
            net_pnl_won = config.order_size_for_hint(position["size_hint"]) * (float(cost_breakdown["net_ret_pct"]) / 100.0)
            state.record_pnl(pnl_won)
            if position["exit_ret_pct"] > 0:
                state.record_profitable_exit()
            else:
                state.record_stop_loss()
            settled.append(
                SettledTrade(
                    event_id=position["event_id"],
                    date=position["date"],
                    ticker=position["ticker"],
                    headline=position["headline"],
                    confidence=position["confidence"],
                    size_hint=position["size_hint"],
                    exit_type=position["exit_type"],
                    exit_ret_pct=round(position["exit_ret_pct"], 4),
                    net_ret_pct=float(cost_breakdown["net_ret_pct"]),
                    pnl_won=round(pnl_won, 0),
                    net_pnl_won=round(net_pnl_won, 0),
                    buy_fee_pct=float(cost_breakdown["buy_fee_pct"]),
                    sell_fee_pct=float(cost_breakdown["sell_fee_pct"]),
                    sell_tax_pct=float(cost_breakdown["sell_tax_pct"]),
                    exit_slippage_pct=float(cost_breakdown["exit_slippage_pct"]),
                    total_cost_pct=float(cost_breakdown["total_cost_pct"]),
                    exit_spread_bps=position["exit_spread_bps"],
                    runtime_entry_slippage_aligned=position["runtime_entry_slippage_aligned"],
                    close_at=position["close_at"].isoformat(),
                )
            )
        open_positions = pending

    for candidate in candidates:
        if current_date != candidate.trade.date:
            if current_date is not None and state is not None:
                settle_until(candidate.detected_at + timedelta(days=1))
            state = GuardrailState(config)
            open_positions = []
            current_date = candidate.trade.date

        settle_until(candidate.detected_at)

        preflight = engine._preflight_decide(
            candidate.trade.headline,
            candidate.context,
            list(candidate.trade.keyword_hits),
            raw_headline=candidate.trade.headline,
            dorg=str(candidate.event.get("dorg", "")),
            run_id="monthly_backtest",
            schema_version=config.schema_version,
        )
        if preflight is not None:
            blocked_by_reason[f"RULE_PREFLIGHT:{preflight.reason}"] += 1
            continue

        result = check_guardrails(
            ticker=candidate.trade.ticker,
            config=config,
            spread_bps=candidate.context.spread_bps if candidate.context.spread_bps is not None else candidate.raw.get("spread_bps"),
            adv_value_20d=candidate.context.adv_value_20d if candidate.context.adv_value_20d is not None else candidate.raw.get("adv_value_20d"),
            ret_today=candidate.context.ret_today if candidate.context.ret_today is not None else candidate.raw.get("ret_today"),
            state=state,
            headline=candidate.trade.headline,
            sector=candidate.sector,
            intraday_value_vs_adv20d=(
                candidate.context.intraday_value_vs_adv20d
                if candidate.context.intraday_value_vs_adv20d is not None
                else candidate.raw.get("intraday_value_vs_adv20d")
            ),
            delay_ms=candidate.delay_ms,
            prior_volume_rate=(
                candidate.context.prior_volume_rate
                if candidate.context.prior_volume_rate is not None
                else candidate.raw.get("prior_volume_rate")
            ),
            quote_temp_stop=(
                candidate.context.quote_temp_stop
                if candidate.context.quote_temp_stop is not None
                else candidate.raw.get("quote_temp_stop")
            ),
            quote_liquidation_trade=(
                candidate.context.quote_liquidation_trade
                if candidate.context.quote_liquidation_trade is not None
                else candidate.raw.get("quote_liquidation_trade")
            ),
            top_ask_notional=(
                candidate.context.top_ask_notional
                if candidate.context.top_ask_notional is not None
                else candidate.raw.get("top_ask_notional")
            ),
            decision_action=Action.BUY,
            decision_confidence=candidate.trade.confidence,
            decision_time_kst=candidate.detected_at,
            decision_hold_minutes=get_max_hold_minutes(candidate.trade.headline, list(candidate.trade.keyword_hits), config),
            decision_size_hint=candidate.trade.size_hint,
        )
        if not result.passed:
            blocked_by_reason[str(result.reason or "UNKNOWN")] += 1
            continue

        assert state is not None
        state.record_buy(candidate.trade.ticker, sector=candidate.sector)
        open_positions.append(
            {
                "event_id": candidate.trade.event_id,
                "date": candidate.trade.date,
                "ticker": candidate.trade.ticker,
                "headline": candidate.trade.headline,
                "confidence": candidate.trade.confidence,
                "size_hint": candidate.trade.size_hint,
                "exit_type": candidate.trade.exit_type,
                "exit_ret_pct": candidate.trade.exit_pnl_pct,
                "exit_spread_bps": _float_or_none(
                    (candidate.snapshot_rows.get(candidate.trade.exit_horizon or "") or {}).get("spread_bps")
                ),
                "runtime_entry_slippage_aligned": _validate_runtime_entry_slippage(
                    candidate.snapshot_rows,
                    candidate.trade.exit_horizon,
                ),
                "close_at": candidate.detected_at + timedelta(minutes=float(candidate.trade.hold_minutes or 0.0)),
                "sector": candidate.sector,
            }
        )

    if state is not None and candidates:
        settle_until(max(candidate.detected_at for candidate in candidates) + timedelta(days=1))

    return {
        "candidate_trade_count": len(candidates),
        "accepted_trade_count": len(settled),
        "blocked_trade_count": sum(blocked_by_reason.values()),
        "blocked_by_reason": dict(sorted(blocked_by_reason.items())),
        "summary": _summarize_pnls(settled),
        "gross_summary": _summarize_pnls(settled),
        "net_summary": _summarize_pnls(settled, return_attr="net_ret_pct", pnl_attr="net_pnl_won"),
        "cost_validation": _summarize_cost_validation(settled),
        "accepted_trades": [asdict(row) for row in settled],
    }


def _summarize_return_values(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "trade_count": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "avg_ret_pct": 0.0,
            "total_ret_pct": 0.0,
            "profit_factor": 0.0,
            "mdd_pct": 0.0,
        }
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    cumulative = 0.0
    peak = 0.0
    mdd = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        mdd = min(mdd, cumulative - peak)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss else (math.inf if gross_win > 0 else 0.0)
    return {
        "trade_count": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(values) * 100, 1),
        "avg_ret_pct": round(sum(values) / len(values), 4),
        "total_ret_pct": round(sum(values), 4),
        "profit_factor": None if not math.isfinite(profit_factor) else round(profit_factor, 2),
        "mdd_pct": round(mdd, 4),
    }


def _version_description(version_tag: str) -> str:
    for row in VERSION_MAP:
        if row["tag"] == version_tag:
            return str(row.get("description", ""))
    return ""


def build_version_comparison(
    log_paths: list[Path],
    *,
    snapshot_dir: Path,
    cost_config: TransactionCostConfig,
) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_root = Path(tmpdir)
        temp_logs = temp_root / "logs"
        temp_logs.mkdir(parents=True, exist_ok=True)
        for path in log_paths:
            (temp_logs / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

        db = TradeDB(temp_root / "trade_history.db")
        try:
            backfill_from_logs(db, temp_logs, snapshot_dir, force=True)
            rows: list[dict[str, Any]] = []
            for version in VERSION_TAGS:
                simulated = simulate_version_on_trades(db, version)
                gross_values = [float(row["exit_ret_pct"]) for row in simulated if row.get("exit_ret_pct") is not None]
                net_values: list[float] = []
                exit_spread_available_count = 0
                exit_spread_missing_count = 0
                for row in simulated:
                    exit_ret_pct = row.get("exit_ret_pct")
                    if exit_ret_pct is None:
                        continue
                    exit_spread_bps = _float_or_none(row.get("exit_spread_bps"))
                    if exit_spread_bps is None:
                        exit_spread_missing_count += 1
                    else:
                        exit_spread_available_count += 1
                    cost_breakdown = _build_cost_breakdown(
                        float(exit_ret_pct),
                        exit_spread_bps=exit_spread_bps,
                        cost_config=cost_config,
                    )
                    net_values.append(float(cost_breakdown["net_ret_pct"]))
                gross_summary = _summarize_return_values(gross_values)
                net_summary = _summarize_return_values(net_values)
                rows.append(
                    {
                        "version": version,
                        "total_trades": gross_summary["trade_count"],
                        "wins": gross_summary["wins"],
                        "losses": gross_summary["losses"],
                        "win_rate": gross_summary["win_rate"],
                        "avg_ret_pct": gross_summary["avg_ret_pct"],
                        "total_ret_pct": gross_summary["total_ret_pct"],
                        "profit_factor": gross_summary["profit_factor"],
                        "mdd_pct": gross_summary["mdd_pct"],
                        "net_avg_ret_pct": net_summary["avg_ret_pct"],
                        "net_total_ret_pct": net_summary["total_ret_pct"],
                        "net_profit_factor": net_summary["profit_factor"],
                        "net_mdd_pct": net_summary["mdd_pct"],
                        "cost_validation": {
                            "exit_spread_available_count": exit_spread_available_count,
                            "exit_spread_missing_count": exit_spread_missing_count,
                        },
                        "description": _version_description(version),
                    }
                )
        finally:
            db.close()
    return rows


def load_supporting_artifacts(analysis_dir: Path) -> dict[str, Any]:
    return {
        "entry_filter_analysis": _load_json(analysis_dir / "entry_filter_analysis_20260328.json"),
        "llm_prompt_eval": _load_json(analysis_dir / "llm_prompt_eval_20260328.json"),
        "pattern_profile": _load_json(analysis_dir / "pattern_profile_20260310_20260327.json"),
    }


def build_best_parameter_set(
    *,
    config: Config,
    deep_backtest_stats: dict[str, Any],
    artifacts: dict[str, Any],
) -> dict[str, Any]:
    exit_candidates = deep_backtest_stats.get("condition_scores", {}).get("exit", {}).get("candidates", [])
    best_exit = exit_candidates[0] if exit_candidates else {}
    llm_eval = artifacts.get("llm_prompt_eval", {})
    historical_run = llm_eval.get("runs", {}).get("historical_actual", {})
    decision_run = llm_eval.get("runs", {}).get("decision_strategy", {})
    return {
        "entry": {
            "max_entry_delay_ms": config.max_entry_delay_ms,
            "min_intraday_value_vs_adv20d": config.min_intraday_value_vs_adv20d,
            "orderbook_bid_ask_ratio_min": config.orderbook_bid_ask_ratio_min,
            "analysis_note": artifacts.get("entry_filter_analysis", {}).get("recommendation", {}).get("summary", ""),
        },
        "exit": best_exit,
        "risk_v2": {
            "max_positions": config.max_positions,
            "consecutive_loss_halt": config.consecutive_loss_halt,
            "recent_trade_window": config.dynamic_daily_loss_recent_trade_window,
            "low_win_rate_multiplier": config.dynamic_daily_loss_low_win_rate_multiplier,
            "zero_win_rate_multiplier": config.dynamic_daily_loss_zero_win_rate_multiplier,
        },
        "llm": {
            "historical_actual_accuracy": historical_run.get("accuracy"),
            "historical_actual_buy_precision": historical_run.get("buy_precision"),
            "historical_actual_avg_exit_pnl_for_predicted_buy": historical_run.get("avg_exit_pnl_for_predicted_buy"),
            "current_replay_status": decision_run.get("status", "missing"),
            "current_replay_error": decision_run.get("error", ""),
        },
    }


def build_report(
    project_root: Path,
    *,
    lookback_days: int = 30,
    lookback_months: int | None = None,
) -> dict[str, Any]:
    log_dir = project_root / "logs"
    snapshot_dir = project_root / "data" / "runtime" / "price_snapshots"
    context_dir = project_root / "data" / "runtime" / "context_cards"
    analysis_dir = project_root / "logs" / "daily_analysis"
    config = Config()
    cost_config = TransactionCostConfig()
    runtime_defaults = ExitSimulationConfig.from_runtime_defaults()
    requested_start, requested_end = resolve_requested_window(
        log_dir,
        lookback_days=lookback_days,
        lookback_months=lookback_months,
    )
    log_paths = select_log_paths(
        log_dir,
        lookback_days=lookback_days,
        lookback_months=lookback_months,
    )
    candidates = collect_candidate_trades(
        log_paths,
        snapshot_dir=snapshot_dir,
        context_dir=context_dir,
        runtime_defaults=runtime_defaults,
    )

    with redirect_stderr(io.StringIO()):
        deep_backtest_stats, trades, _shadow = analyze_paths(
            log_paths,
            snapshot_dir=snapshot_dir,
            runtime_defaults=runtime_defaults,
        )

    current_strategy = simulate_current_strategy(candidates, config=config, cost_config=cost_config)
    version_comparison = build_version_comparison(log_paths, snapshot_dir=snapshot_dir, cost_config=cost_config)
    artifacts = load_supporting_artifacts(analysis_dir)
    latest_date = log_paths[-1].stem.replace("kindshot_", "") if log_paths else ""
    earliest_date = log_paths[0].stem.replace("kindshot_", "") if log_paths else ""
    requested_from = requested_start.strftime("%Y%m%d") if requested_start is not None else ""
    requested_to = requested_end.strftime("%Y%m%d") if requested_end is not None else ""
    requested_calendar_days = (
        max(0, (requested_end - requested_start).days + 1)
        if requested_start is not None and requested_end is not None
        else 0
    )
    covered_trade_days = len({trade.date for trade in trades})

    llm_eval = artifacts.get("llm_prompt_eval", {})
    llm_decision_status = llm_eval.get("runs", {}).get("decision_strategy", {})
    limitations = []
    if requested_from and requested_to and (earliest_date != requested_from or latest_date != requested_to):
        limitations.append(
            f"Requested window {requested_from}~{requested_to} is only partially covered by local evidence {earliest_date or 'N/A'}~{latest_date or 'N/A'}."
        )
    limitations.append(
        "Current opaque LLM replay is blocked locally, so the current-strategy estimate reuses historical logged BUY decisions and re-applies only deterministic current guards plus current exit/risk logic."
    )
    if current_strategy["cost_validation"]["exit_spread_missing_count"] > 0:
        limitations.append(
            "Exit slippage is modeled only when the exit-horizon snapshot contains spread_bps; missing spreads are reported as uncovered instead of guessed."
        )
    if llm_decision_status.get("error"):
        limitations.append(llm_decision_status["error"])

    return {
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "requested_lookback_days": lookback_days,
            "requested_window": {
                "from": requested_from,
                "to": requested_to,
            },
            "available_window": {
                "from": earliest_date,
                "to": latest_date,
                "log_count": len(log_paths),
            },
            "coverage": {
                "requested_calendar_days": requested_calendar_days,
                "covered_log_days": len(log_paths),
                "covered_trade_days": covered_trade_days,
                "coverage_ratio": round(len(log_paths) / requested_calendar_days, 4) if requested_calendar_days else 0.0,
                "requested_months": lookback_months,
            },
            "llm_replay_status": llm_decision_status.get("status", "missing"),
        },
        "cost_model": cost_config.to_dict(),
        "current_strategy_estimate": current_strategy,
        "version_comparison": version_comparison,
        "best_parameter_set": build_best_parameter_set(
            config=config,
            deep_backtest_stats=deep_backtest_stats,
            artifacts=artifacts,
        ),
        "supporting_artifacts": {
            "entry_filter_analysis": artifacts.get("entry_filter_analysis", {}),
            "llm_prompt_eval": artifacts.get("llm_prompt_eval", {}),
        },
        "limitations": limitations,
    }


def render_text(report: dict[str, Any]) -> str:
    meta = report["meta"]
    cost_model = report.get("cost_model") or TransactionCostConfig().to_dict()
    current = report["current_strategy_estimate"]
    gross_summary = current.get("gross_summary") or current["summary"]
    net_summary = current.get("net_summary") or current["summary"]
    coverage = meta.get("coverage", {})
    lines = [
        "Kindshot Monthly Full-Strategy Backtest",
        (
            f"requested_window={meta.get('requested_window', {}).get('from', '')}"
            f"~{meta.get('requested_window', {}).get('to', '')}"
        ),
        f"covered_window={meta['available_window']['from']}~{meta['available_window']['to']} logs={meta['available_window']['log_count']}",
        (
            f"coverage=log_days:{coverage.get('covered_log_days', 0)}/"
            f"{coverage.get('requested_calendar_days', 0)} ratio={coverage.get('coverage_ratio', 0.0):.4f}"
        ),
        f"llm_replay_status={meta['llm_replay_status']}",
        (
            "cost_model="
            f"buy_fee_bps={cost_model['buy_fee_bps']} sell_fee_bps={cost_model['sell_fee_bps']} "
            f"sell_tax_bps={cost_model['sell_tax_bps']} exit_slippage=half_spread"
        ),
        "",
        "[1] Current strategy estimate",
        f"candidates={current['candidate_trade_count']} accepted={current['accepted_trade_count']} blocked={current['blocked_trade_count']}",
        (
            f"gross_win_rate={gross_summary['win_rate_pct']:.1f}% gross_total_ret_pct={gross_summary['total_ret_pct']:+.4f}% "
            f"gross_total_pnl_won={int(gross_summary['total_pnl_won'])}"
        ),
        (
            f"net_win_rate={net_summary['win_rate_pct']:.1f}% net_total_ret_pct={net_summary['total_ret_pct']:+.4f}% "
            f"net_total_pnl_won={int(net_summary['total_pnl_won'])}"
        ),
        "blocked_by_reason:",
    ]
    for reason, count in current["blocked_by_reason"].items():
        lines.append(f"  - {reason}: {count}")
    cost_validation = current["cost_validation"]
    validated_trade_count = cost_validation.get("trade_count", current.get("accepted_trade_count", 0))
    lines.extend(
        [
            "Cost validation:",
            (
                "  - entry_slippage_runtime_aligned="
                f"{cost_validation['runtime_entry_slippage_aligned_count']}/{validated_trade_count}"
            ),
            (
                "  - exit_spread_coverage="
                f"{cost_validation['exit_spread_available_count']}/{validated_trade_count} "
                f"missing={cost_validation['exit_spread_missing_count']}"
            ),
        ]
    )

    lines.extend(["", "[2] Version comparison (v64~v70)"])
    for row in report["version_comparison"]:
        pf = "-" if row["profit_factor"] is None else f"{row['profit_factor']:.2f}"
        net_pf = "-" if row["net_profit_factor"] is None else f"{row['net_profit_factor']:.2f}"
        lines.append(
            f"  - {row['version']}: trades={row['total_trades']} win_rate={row['win_rate']:.1f}% "
            f"gross_total={row['total_ret_pct']:+.4f}% net_total={row['net_total_ret_pct']:+.4f}% "
            f"gross_pf={pf} net_pf={net_pf} mdd={row['mdd_pct']:+.4f}%"
        )

    best = report["best_parameter_set"]
    entry = best["entry"]
    exit_candidate = best["exit"]
    lines.extend(
        [
            "",
            "[3] Best parameter set",
            (
                "entry="
                f"delay_ms<{entry['max_entry_delay_ms']} "
                f"intraday_value_vs_adv20d>={entry['min_intraday_value_vs_adv20d']} "
                f"orderbook_ratio>={entry['orderbook_bid_ask_ratio_min']}"
            ),
        ]
    )
    if exit_candidate:
        params = exit_candidate["params"]
        lines.append(
            "exit="
            f"tp={params['paper_take_profit_pct']} sl={params['paper_stop_loss_pct']} "
            f"trail_activation={params['trailing_stop_activation_pct']} "
            f"trail=({params['trailing_stop_early_pct']},{params['trailing_stop_mid_pct']},{params['trailing_stop_late_pct']}) "
            f"max_hold={params['max_hold_minutes']} t5m_loss_exit={params['t5m_loss_exit_enabled']}"
        )
    risk = best["risk_v2"]
    lines.append(
        "risk_v2="
        f"max_positions={risk['max_positions']} consecutive_loss_halt={risk['consecutive_loss_halt']} "
        f"recent_trade_window={risk['recent_trade_window']}"
    )
    llm = best["llm"]
    lines.append(
        "llm="
        f"historical_accuracy={llm['historical_actual_accuracy']} "
        f"buy_precision={llm['historical_actual_buy_precision']} "
        f"current_replay_status={llm['current_replay_status']}"
    )

    lines.extend(["", "[4] Limitations"])
    for row in report["limitations"]:
        lines.append(f"  - {row}")

    return "\n".join(lines) + "\n"


def write_report(project_root: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    out_dir = project_root / "logs" / "daily_analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.now().strftime("%Y%m%d")
    json_path = out_dir / f"monthly_full_strategy_backtest_{date_tag}.json"
    txt_path = out_dir / f"monthly_full_strategy_backtest_{date_tag}.txt"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    txt_path.write_text(render_text(report), encoding="utf-8")
    return json_path, txt_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--lookback-months", type=int)
    args = parser.parse_args()

    report = build_report(
        PROJECT_ROOT,
        lookback_days=args.lookback_days,
        lookback_months=args.lookback_months,
    )
    json_path, txt_path = write_report(PROJECT_ROOT, report)
    print(f"saved_json={json_path}")
    print(f"saved_text={txt_path}")
    print(render_text(report))


if __name__ == "__main__":
    main()
