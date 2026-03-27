#!/usr/bin/env python3
"""Deep backtest analysis for Kindshot trading logs.

This script reconstructs executed BUY trades from local `kindshot_*.jsonl`
logs, enriches them with news-type / time-of-day metadata, scores entry and
exit conditions, and emits both operator-readable text and structured JSON.
"""

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kindshot.config import Config
from kindshot.guardrails import get_dynamic_stop_loss_pct, get_dynamic_tp_pct
from kindshot.hold_profile import resolve_hold_profile
from kindshot.tz import KST as _KST

HORIZON_ORDER = ["t+30s", "t+1m", "t+2m", "t+5m", "t+10m", "t+15m", "t+20m", "t+30m", "close"]
HORIZON_MINUTES = {
    "t+30s": 0.5,
    "t+1m": 1.0,
    "t+2m": 2.0,
    "t+5m": 5.0,
    "t+10m": 10.0,
    "t+15m": 15.0,
    "t+20m": 20.0,
    "t+30m": 30.0,
    "close": 390.0,
}
NEWS_TYPE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("shareholder_return", ("자사주 소각", "자사주소각", "자사주 취득", "자사주취득", "자사주 매입", "자사주매입", "배당", "주주환원", "공개매수")),
    ("mna", ("인수", "합병", "M&A", "경영권 분쟁", "경영권분쟁", "위임장 대결")),
    ("clinical_regulatory", ("FDA", "품목허가", "임상3상", "임상 3상", "임상2상", "임상 2상", "승인", "허가", "특허", "AACR")),
    ("contract", ("공급계약", "공급 계약", "수주", "납품계약", "독점 공급", "조달청", "정부 조달", "양산 개시", "첫 수주", "최초 수주")),
    ("earnings_turnaround", ("실적", "흑자전환", "흑자 전환", "어닝", "사상 최대", "사상최대", "역대 최대", "역대최대")),
    ("product_technology", ("개발", "출시", "론칭", "기술이전", "기술수출", "라이선스 아웃", "CDMO", "플랫폼", "신제품")),
    ("policy_funding", ("국책", "정책", "지원", "업무협약", "MOU", "투자유치", "보조금", "수주잔고")),
]
CONFIDENCE_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("75-77", 75, 77),
    ("78-80", 78, 80),
    ("81-85", 81, 85),
    ("86-90", 86, 90),
    ("91+", 91, None),
)


@dataclass(frozen=True)
class ExitSimulationConfig:
    paper_take_profit_pct: float = 2.0
    paper_stop_loss_pct: float = -1.5
    trailing_stop_enabled: bool = True
    trailing_stop_activation_pct: float = 0.5
    trailing_stop_early_pct: float = 0.5
    trailing_stop_mid_pct: float = 0.8
    trailing_stop_late_pct: float = 1.0
    max_hold_minutes: int = 15
    t5m_loss_exit_enabled: bool = True
    t5m_profit_trailing_pct: float = 0.5
    session_early_sl_multiplier: float = 0.7
    session_late_max_hold_divisor: float = 2.0
    stale_threshold_pct_default: float = 0.2
    stale_min_minutes: float = 3.0

    @classmethod
    def from_runtime_defaults(cls) -> "ExitSimulationConfig":
        cfg = Config()
        return cls(
            paper_take_profit_pct=cfg.paper_take_profit_pct,
            paper_stop_loss_pct=cfg.paper_stop_loss_pct,
            trailing_stop_enabled=cfg.trailing_stop_enabled,
            trailing_stop_activation_pct=cfg.trailing_stop_activation_pct,
            trailing_stop_early_pct=cfg.trailing_stop_early_pct,
            trailing_stop_mid_pct=cfg.trailing_stop_mid_pct,
            trailing_stop_late_pct=cfg.trailing_stop_late_pct,
            max_hold_minutes=cfg.max_hold_minutes,
            t5m_loss_exit_enabled=cfg.t5m_loss_exit_enabled,
            t5m_profit_trailing_pct=cfg.t5m_profit_trailing_pct,
            session_early_sl_multiplier=cfg.session_early_sl_multiplier,
            session_late_max_hold_divisor=cfg.session_late_max_hold_divisor,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_take_profit_pct": self.paper_take_profit_pct,
            "paper_stop_loss_pct": self.paper_stop_loss_pct,
            "trailing_stop_enabled": self.trailing_stop_enabled,
            "trailing_stop_activation_pct": self.trailing_stop_activation_pct,
            "trailing_stop_early_pct": self.trailing_stop_early_pct,
            "trailing_stop_mid_pct": self.trailing_stop_mid_pct,
            "trailing_stop_late_pct": self.trailing_stop_late_pct,
            "max_hold_minutes": self.max_hold_minutes,
            "t5m_loss_exit_enabled": self.t5m_loss_exit_enabled,
            "t5m_profit_trailing_pct": self.t5m_profit_trailing_pct,
            "session_early_sl_multiplier": self.session_early_sl_multiplier,
            "session_late_max_hold_divisor": self.session_late_max_hold_divisor,
        }


@dataclass
class ExitSimulationResult:
    exit_type: str
    exit_horizon: str | None
    exit_pnl_pct: float
    hold_minutes: float


@dataclass
class Trade:
    event_id: str
    date: str
    ticker: str
    headline: str
    bucket: str
    confidence: int
    size_hint: str
    reason: str
    decision_source: str
    detected_at: str
    source: str = ""
    dorg: str = ""
    keyword_hits: list[str] = field(default_factory=list)
    entry_price: float = 0.0
    snapshots: dict[str, float] = field(default_factory=dict)
    exit_type: str = ""
    exit_horizon: str | None = None
    exit_pnl_pct: float = 0.0
    max_gain_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    hold_minutes: float = 0.0
    hold_profile_minutes: int = 0
    hold_profile_keyword: str | None = None
    news_type: str = "other_positive"
    hour: int = -1
    hour_bucket: str = "unknown"

    def to_row(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "date": self.date,
            "ticker": self.ticker,
            "headline": self.headline,
            "bucket": self.bucket,
            "confidence": self.confidence,
            "size_hint": self.size_hint,
            "reason": self.reason,
            "decision_source": self.decision_source,
            "detected_at": self.detected_at,
            "source": self.source,
            "dorg": self.dorg,
            "keyword_hits": list(self.keyword_hits),
            "entry_price": round(self.entry_price, 4),
            "snapshots": {k: round(v, 4) for k, v in self.snapshots.items()},
            "exit_type": self.exit_type,
            "exit_horizon": self.exit_horizon,
            "exit_pnl_pct": round(self.exit_pnl_pct, 4),
            "max_gain_pct": round(self.max_gain_pct, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "hold_minutes": self.hold_minutes,
            "hold_profile_minutes": self.hold_profile_minutes,
            "hold_profile_keyword": self.hold_profile_keyword,
            "news_type": self.news_type,
            "hour": self.hour,
            "hour_bucket": self.hour_bucket,
        }


@dataclass
class GuardrailBlockRecord:
    event_id: str
    date: str
    ticker: str
    headline: str
    bucket: str
    confidence: int
    skip_reason: str
    detected_at: str
    news_type: str
    hour: int
    hour_bucket: str
    shadow_available: bool = False

    def to_row(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "date": self.date,
            "ticker": self.ticker,
            "headline": self.headline,
            "bucket": self.bucket,
            "confidence": self.confidence,
            "skip_reason": self.skip_reason,
            "detected_at": self.detected_at,
            "news_type": self.news_type,
            "hour": self.hour,
            "hour_bucket": self.hour_bucket,
            "shadow_available": self.shadow_available,
        }


def _parse_kst_datetime(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_KST)
    return dt.astimezone(_KST)


def _parse_kst_hour(ts: str) -> int:
    dt = _parse_kst_datetime(ts)
    return dt.hour if dt is not None else -1


def _hour_bucket(hour: int) -> str:
    if hour < 0:
        return "unknown"
    if hour < 9:
        return "pre_open"
    if hour == 9:
        return "open"
    if hour == 10:
        return "mid_morning"
    if 11 <= hour <= 13:
        return "midday"
    if hour == 14:
        return "afternoon"
    return "late"


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _confidence_band(confidence: int) -> str:
    for label, low, high in CONFIDENCE_BANDS:
        if confidence >= low and (high is None or confidence <= high):
            return label
    return "<75"


def classify_news_type(
    headline: str,
    keyword_hits: list[str] | None = None,
    bucket: str = "",
    source: str = "",
    dorg: str = "",
) -> str:
    haystacks = [headline, bucket, source, dorg, *(keyword_hits or [])]
    for label, patterns in NEWS_TYPE_RULES:
        if any(pattern in text for text in haystacks for pattern in patterns):
            return label
    return "other_positive"


def load_day(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec_type = rec.get("type", rec.get("record_type", ""))
            if rec_type == "event":
                events.append(rec)
            elif rec_type == "decision":
                decisions.append(rec)
            elif rec_type == "price_snapshot":
                snapshots.append(rec)
    return events, decisions, snapshots


def _append_runtime_snapshots(date_str: str, snapshots: list[dict[str, Any]], snapshot_dir: Path | None) -> None:
    if snapshot_dir is None:
        return
    snap_file = snapshot_dir / f"{date_str}.jsonl"
    if not snap_file.exists():
        return
    with snap_file.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "price_snapshot":
                snapshots.append(rec)


def _snapshot_return_pct(snapshot_rows: dict[str, dict[str, Any]], horizon: str, t0_px: float) -> float | None:
    row = snapshot_rows.get(horizon)
    if row is None:
        return None
    ret = row.get("ret_long_vs_t0")
    if isinstance(ret, (int, float)):
        return float(ret) * 100
    px = row.get("px")
    if not isinstance(px, (int, float)) or t0_px <= 0:
        return None
    return (float(px) - t0_px) / t0_px * 100


def _session_adjusted_sl(config: ExitSimulationConfig, hour: int, minute: int, base_sl: float) -> float:
    if hour == 9 and minute < 30:
        return base_sl * config.session_early_sl_multiplier
    if hour >= 14:
        return base_sl * 0.8
    return base_sl


def _session_adjusted_max_hold(config: ExitSimulationConfig, hour: int, base_max_hold: int) -> int:
    if base_max_hold == 0:
        return 0
    if hour >= 14:
        return max(5, int(base_max_hold / config.session_late_max_hold_divisor))
    return base_max_hold


def _trailing_gap(config: ExitSimulationConfig, hold_minutes: int, elapsed_minutes: float, t5m_profitable: bool) -> float:
    if t5m_profitable:
        return config.t5m_profit_trailing_pct
    if elapsed_minutes < 5:
        base = config.trailing_stop_early_pct
    elif elapsed_minutes < 30:
        base = config.trailing_stop_mid_pct
    else:
        base = config.trailing_stop_late_pct
    if hold_minutes == 0:
        return base * 1.5
    if hold_minutes <= 20:
        return base * 0.85
    return base


def _stale_threshold(config: ExitSimulationConfig, confidence: int, effective_sl: float) -> float:
    if confidence >= 85:
        return max(0.5, abs(effective_sl) * 0.4)
    if confidence >= 80:
        return 0.3
    return config.stale_threshold_pct_default


def simulate_trade_exit(trade: Trade, config: ExitSimulationConfig) -> ExitSimulationResult:
    dt = _parse_kst_datetime(trade.detected_at)
    hour = dt.hour if dt is not None else -1
    minute = dt.minute if dt is not None else 0
    hold_profile_minutes, _matched = resolve_hold_profile(trade.headline, trade.keyword_hits, config)
    effective_tp = get_dynamic_tp_pct(config, trade.confidence, hold_profile_minutes)
    effective_sl = get_dynamic_stop_loss_pct(config, trade.confidence, hold_profile_minutes)
    effective_sl = _session_adjusted_sl(config, hour, minute, effective_sl)
    adjusted_max_hold = _session_adjusted_max_hold(config, hour, hold_profile_minutes)

    peak = 0.0
    t5m_profitable: bool | None = None
    last_horizon: str | None = None
    last_ret = 0.0

    for horizon in HORIZON_ORDER:
        ret_pct = trade.snapshots.get(horizon)
        if ret_pct is None:
            continue
        elapsed_minutes = HORIZON_MINUTES[horizon]
        last_horizon = horizon
        last_ret = ret_pct
        peak = max(peak, ret_pct)

        if effective_tp > 0 and ret_pct >= effective_tp:
            return ExitSimulationResult("TP", horizon, ret_pct, elapsed_minutes)
        if effective_sl < 0 and ret_pct <= effective_sl:
            return ExitSimulationResult("SL", horizon, ret_pct, elapsed_minutes)

        if config.t5m_loss_exit_enabled and elapsed_minutes >= 5 and adjusted_max_hold != 0 and t5m_profitable is None:
            t5m_profitable = ret_pct > 0
            if not t5m_profitable and ret_pct <= 0:
                return ExitSimulationResult("T5M_LOSS_EXIT", horizon, ret_pct, elapsed_minutes)

        if config.trailing_stop_enabled and peak >= config.trailing_stop_activation_pct:
            trail_gap = _trailing_gap(config, hold_profile_minutes, elapsed_minutes, t5m_profitable is True)
            if ret_pct <= peak - trail_gap:
                return ExitSimulationResult("TRAILING", horizon, ret_pct, elapsed_minutes)

        if adjusted_max_hold > 0 and elapsed_minutes == adjusted_max_hold:
            return ExitSimulationResult("MAX_HOLD", horizon, ret_pct, elapsed_minutes)

        if adjusted_max_hold != 0 and elapsed_minutes >= config.stale_min_minutes:
            stale_pct = _stale_threshold(config, trade.confidence, effective_sl)
            if abs(ret_pct) < stale_pct:
                return ExitSimulationResult("STALE", horizon, ret_pct, elapsed_minutes)

    if last_horizon is None:
        return ExitSimulationResult("NO_DATA", None, 0.0, 0.0)
    exit_type = "CLOSE" if last_horizon == "close" else "CLOSE"
    return ExitSimulationResult(exit_type, last_horizon, last_ret, HORIZON_MINUTES[last_horizon])


def _trade_from_event(
    ev: dict[str, Any],
    decision: dict[str, Any],
    snapshot_rows: dict[str, dict[str, Any]],
    date_str: str,
    default_config: ExitSimulationConfig,
) -> Trade | None:
    t0_row = snapshot_rows.get("t0", {})
    t0_px = t0_row.get("px")
    if not isinstance(t0_px, (int, float)) or float(t0_px) <= 0:
        return None
    returns: dict[str, float] = {}
    max_gain = 0.0
    max_dd = 0.0
    for horizon in HORIZON_ORDER:
        ret_pct = _snapshot_return_pct(snapshot_rows, horizon, float(t0_px))
        if ret_pct is None:
            continue
        returns[horizon] = ret_pct
        max_gain = max(max_gain, ret_pct)
        max_dd = min(max_dd, ret_pct)

    if not returns:
        return None

    detected_at = str(ev.get("detected_at", ""))
    hour = _parse_kst_hour(detected_at)
    hold_profile_minutes, hold_profile_keyword = resolve_hold_profile(
        str(ev.get("headline", "")),
        list(ev.get("keyword_hits") or []),
        default_config,
    )
    trade = Trade(
        event_id=str(ev.get("event_id", "")),
        date=date_str,
        ticker=str(ev.get("ticker", "")),
        headline=str(ev.get("headline", ""))[:120],
        bucket=str(ev.get("bucket", "")),
        confidence=int(decision.get("confidence", ev.get("decision_confidence", 0)) or 0),
        size_hint=str(decision.get("size_hint", ev.get("decision_size_hint", "M")) or "M"),
        reason=str(decision.get("reason", ev.get("decision_reason", "")))[:120],
        decision_source=str(decision.get("decision_source", "")),
        detected_at=detected_at,
        source=str(ev.get("source", "")),
        dorg=str(ev.get("dorg", "")),
        keyword_hits=list(ev.get("keyword_hits") or []),
        entry_price=float(t0_px),
        snapshots=returns,
        max_gain_pct=max_gain,
        max_drawdown_pct=max_dd,
        hold_profile_minutes=hold_profile_minutes,
        hold_profile_keyword=hold_profile_keyword,
        news_type=classify_news_type(
            str(ev.get("headline", "")),
            list(ev.get("keyword_hits") or []),
            bucket=str(ev.get("bucket", "")),
            source=str(ev.get("source", "")),
            dorg=str(ev.get("dorg", "")),
        ),
        hour=hour,
        hour_bucket=_hour_bucket(hour),
    )
    exit_result = simulate_trade_exit(trade, default_config)
    trade.exit_type = exit_result.exit_type
    trade.exit_horizon = exit_result.exit_horizon
    trade.exit_pnl_pct = exit_result.exit_pnl_pct
    trade.hold_minutes = exit_result.hold_minutes
    return trade


def build_trades(
    events: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    date_str: str,
    default_config: ExitSimulationConfig,
) -> list[Trade]:
    decision_map: dict[str, dict[str, Any]] = {}
    for dec in decisions:
        if dec.get("action") == "BUY":
            decision_map[str(dec.get("event_id", ""))] = dec

    snapshot_map: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for snap in snapshots:
        event_id = str(snap.get("event_id", ""))
        horizon = str(snap.get("horizon", ""))
        if event_id and horizon:
            snapshot_map[event_id][horizon] = snap

    trades: list[Trade] = []
    for ev in events:
        event_id = str(ev.get("event_id", ""))
        if ev.get("decision_action") != "BUY":
            continue
        skip_stage = ev.get("skip_stage")
        if skip_stage not in (None, "", "None"):
            continue
        decision = decision_map.get(event_id, {})
        trade = _trade_from_event(ev, decision, snapshot_map.get(event_id, {}), date_str, default_config)
        if trade is not None:
            trades.append(trade)
    return trades


def build_shadow_trades(
    events: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    date_str: str,
    default_config: ExitSimulationConfig,
) -> list[Trade]:
    event_map = {str(ev.get("event_id", "")): ev for ev in events}
    shadow_rows: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for snap in snapshots:
        event_id = str(snap.get("event_id", ""))
        horizon = str(snap.get("horizon", ""))
        if event_id.startswith("shadow_") and horizon:
            shadow_rows[event_id][horizon] = snap

    trades: list[Trade] = []
    for shadow_event_id, snapshot_rows in shadow_rows.items():
        original_id = shadow_event_id.replace("shadow_", "", 1)
        ev = event_map.get(original_id, {})
        if not ev:
            continue
        decision_proxy = {
            "confidence": ev.get("decision_confidence", 0),
            "size_hint": ev.get("decision_size_hint", "M"),
            "reason": ev.get("skip_reason", ""),
            "decision_source": "SHADOW",
        }
        trade = _trade_from_event(ev, decision_proxy, snapshot_rows, date_str, default_config)
        if trade is not None:
            trade.event_id = shadow_event_id
            trade.decision_source = "SHADOW"
            trades.append(trade)
    return trades


def build_guardrail_blocks(
    events: list[dict[str, Any]],
    date_str: str,
    *,
    shadow_source_ids: set[str] | None = None,
) -> list[GuardrailBlockRecord]:
    shadow_source_ids = shadow_source_ids or set()
    blocks: list[GuardrailBlockRecord] = []
    for ev in events:
        if ev.get("decision_action") != "BUY":
            continue
        if ev.get("skip_stage") != "GUARDRAIL":
            continue
        detected_at = str(ev.get("detected_at", ""))
        headline = str(ev.get("headline", ""))
        bucket = str(ev.get("bucket", ""))
        keyword_hits = ev.get("keyword_hits", [])
        if not isinstance(keyword_hits, list):
            keyword_hits = []
        blocks.append(
            GuardrailBlockRecord(
                event_id=str(ev.get("event_id", "")),
                date=date_str,
                ticker=str(ev.get("ticker", "")),
                headline=headline,
                bucket=bucket,
                confidence=int(ev.get("decision_confidence") or 0),
                skip_reason=str(ev.get("skip_reason") or ev.get("guardrail_result") or "UNKNOWN"),
                detected_at=detected_at,
                news_type=classify_news_type(
                    headline,
                    keyword_hits,
                    bucket,
                    str(ev.get("source", "")),
                    str(ev.get("dorg", "")),
                ),
                hour=_parse_kst_hour(detected_at),
                hour_bucket=_hour_bucket(_parse_kst_hour(detected_at)),
                shadow_available=str(ev.get("event_id", "")) in shadow_source_ids,
            )
        )
    return blocks


def _summarize_pnls(pnls: list[float], ordered_pnls: list[float]) -> dict[str, Any]:
    total = len(pnls)
    if total == 0:
        return {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "total_pnl": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 0.0,
            "median_pnl": 0.0,
            "mdd_pct": 0.0,
        }

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    cumulative = 0.0
    peak = 0.0
    mdd = 0.0
    for pnl in ordered_pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        mdd = min(mdd, cumulative - peak)

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss else math.inf
    return {
        "count": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / total * 100,
        "avg_pnl": sum(pnls) / total,
        "total_pnl": sum(pnls),
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "profit_factor": profit_factor,
        "median_pnl": _median(pnls),
        "mdd_pct": mdd,
    }


def summarize_trade_group(trades: list[Trade]) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda trade: trade.detected_at)
    pnls = [trade.exit_pnl_pct for trade in ordered]
    stats = _summarize_pnls(pnls, pnls)
    if trades:
        stats["max_single_gain"] = max(trade.max_gain_pct for trade in trades)
        stats["max_single_loss"] = min(trade.max_drawdown_pct for trade in trades)
    else:
        stats["max_single_gain"] = 0.0
        stats["max_single_loss"] = 0.0
    return stats


def _stats_dict(trades: list[Trade]) -> dict[str, Any]:
    stats = summarize_trade_group(trades)
    return {
        "count": stats["count"],
        "win_rate": round(stats["win_rate"], 1),
        "avg_pnl": round(stats["avg_pnl"], 3),
        "total_pnl": round(stats["total_pnl"], 3),
        "avg_win": round(stats["avg_win"], 3),
        "avg_loss": round(stats["avg_loss"], 3),
        "profit_factor": None if not math.isfinite(stats["profit_factor"]) else round(stats["profit_factor"], 2),
        "median_pnl": round(stats["median_pnl"], 3),
        "mdd_pct": round(stats["mdd_pct"], 3),
    }


def _group_stats(trades: list[Trade], key_fn) -> dict[str, Any]:
    grouped: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        grouped[str(key_fn(trade))].append(trade)
    return {key: _stats_dict(grouped[key]) for key in sorted(grouped)}


def _nested_group_stats(trades: list[Trade], outer_key_fn, inner_key_fn) -> dict[str, Any]:
    grouped: dict[str, dict[str, list[Trade]]] = defaultdict(lambda: defaultdict(list))
    for trade in trades:
        grouped[str(outer_key_fn(trade))][str(inner_key_fn(trade))].append(trade)
    result: dict[str, Any] = {}
    for outer_key in sorted(grouped):
        result[outer_key] = {inner_key: _stats_dict(grouped[outer_key][inner_key]) for inner_key in sorted(grouped[outer_key])}
    return result


def _count_group(rows: list[Any], key_fn) -> dict[str, int]:
    grouped: Counter[str] = Counter()
    for row in rows:
        grouped[str(key_fn(row))] += 1
    return dict(sorted(grouped.items()))


def build_guardrail_review(
    trades: list[Trade],
    guardrail_blocks: list[GuardrailBlockRecord],
    shadow_trades: list[Trade],
) -> dict[str, Any]:
    total_inline_buy = len(trades) + len(guardrail_blocks)
    shadow_by_source: dict[str, list[Trade]] = defaultdict(list)
    for trade in shadow_trades:
        shadow_by_source[trade.event_id.replace("shadow_", "", 1)].append(trade)

    by_reason: dict[str, Any] = {}
    reason_groups: dict[str, list[GuardrailBlockRecord]] = defaultdict(list)
    for row in guardrail_blocks:
        reason_groups[row.skip_reason].append(row)
    for reason, group in sorted(reason_groups.items()):
        shadow_group: list[Trade] = []
        for row in group:
            shadow_group.extend(shadow_by_source.get(row.event_id, []))
        by_reason[reason] = {
            "count": len(group),
            "share_pct": round(len(group) / len(guardrail_blocks) * 100, 1) if guardrail_blocks else 0.0,
            "shadow_count": len(shadow_group),
            "shadow_summary": _stats_dict(shadow_group) if shadow_group else None,
        }

    return {
        "inline_buy_total": total_inline_buy,
        "passed_buy_count": len(trades),
        "blocked_buy_count": len(guardrail_blocks),
        "block_rate_pct": round(len(guardrail_blocks) / total_inline_buy * 100, 1) if total_inline_buy else 0.0,
        "replayed_passed_buy_count": len(trades),
        "shadow_blocked_buy_count": len(shadow_trades),
        "shadow_coverage_pct": round(len(shadow_trades) / len(guardrail_blocks) * 100, 1) if guardrail_blocks else 0.0,
        "passed_summary": _stats_dict(trades),
        "blocked_shadow_summary": _stats_dict(shadow_trades) if shadow_trades else None,
        "by_reason": by_reason,
        "by_confidence_band": _count_group(guardrail_blocks, lambda row: _confidence_band(row.confidence)),
        "by_hour": _count_group(guardrail_blocks, lambda row: f"{row.hour:02d}" if row.hour >= 0 else "unknown"),
        "by_hour_bucket": _count_group(guardrail_blocks, lambda row: row.hour_bucket),
        "by_news_type": _count_group(guardrail_blocks, lambda row: row.news_type),
        "near_threshold": [
            row.to_row()
            for row in sorted(guardrail_blocks, key=lambda item: item.detected_at)
            if 75 <= row.confidence <= 80
        ][:10],
        "blocked_rows": [row.to_row() for row in sorted(guardrail_blocks, key=lambda item: item.detected_at)],
    }


def _condition_score(candidate_stats: dict[str, Any], baseline_stats: dict[str, Any]) -> float:
    profit_factor = candidate_stats["profit_factor"]
    pf_component = min(4.0, profit_factor if math.isfinite(profit_factor) else 4.0)
    sample_factor = min(1.0, candidate_stats["count"] / 5.0)
    return sample_factor * (
        (candidate_stats["avg_pnl"] - baseline_stats["avg_pnl"]) * 20.0
        + (candidate_stats["win_rate"] - baseline_stats["win_rate"]) / 8.0
        + pf_component
        - abs(candidate_stats["mdd_pct"]) / 3.0
    )


def rank_entry_conditions(trades: list[Trade], baseline_stats: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    definitions = [
        ("confidence_band", lambda trade: _confidence_band(trade.confidence)),
        ("hour", lambda trade: f"{trade.hour:02d}" if trade.hour >= 0 else "unknown"),
        ("hour_bucket", lambda trade: trade.hour_bucket),
        ("news_type", lambda trade: trade.news_type),
        ("ticker", lambda trade: trade.ticker or "unknown"),
        ("source", lambda trade: trade.decision_source or "unknown"),
    ]
    for category, key_fn in definitions:
        grouped: dict[str, list[Trade]] = defaultdict(list)
        for trade in trades:
            grouped[str(key_fn(trade))].append(trade)
        for label, grouped_trades in grouped.items():
            if len(grouped_trades) < 2:
                continue
            stats = summarize_trade_group(grouped_trades)
            candidates.append(
                {
                    "category": category,
                    "label": label,
                    **_stats_dict(grouped_trades),
                    "score": round(_condition_score(stats, baseline_stats), 3),
                }
            )

    intersection_groups: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        key = f"{trade.news_type} x {trade.hour_bucket}"
        intersection_groups[key].append(trade)
    for label, grouped_trades in intersection_groups.items():
        if len(grouped_trades) < 2:
            continue
        stats = summarize_trade_group(grouped_trades)
        candidates.append(
            {
                "category": "news_type_x_hour_bucket",
                "label": label,
                **_stats_dict(grouped_trades),
                "score": round(_condition_score(stats, baseline_stats), 3),
            }
        )

    return sorted(
        candidates,
        key=lambda row: (row["score"], row["total_pnl"], row["count"]),
        reverse=True,
    )


def _evaluate_exit_candidates(
    trades: list[Trade],
    baseline_config: ExitSimulationConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    baseline_rows: list[dict[str, Any]] = []
    baseline_ordered_pnls: list[float] = []
    for trade in sorted(trades, key=lambda item: item.detected_at):
        simulated = simulate_trade_exit(trade, baseline_config)
        baseline_rows.append({"exit_type": simulated.exit_type, "exit_pnl_pct": simulated.exit_pnl_pct})
        baseline_ordered_pnls.append(simulated.exit_pnl_pct)

    baseline_summary = _summarize_pnls(
        [row["exit_pnl_pct"] for row in baseline_rows],
        baseline_ordered_pnls,
    )
    baseline_candidate = {
        "params": baseline_config.to_dict(),
        **{
            "count": baseline_summary["count"],
            "win_rate": round(baseline_summary["win_rate"], 1),
            "avg_pnl": round(baseline_summary["avg_pnl"], 3),
            "total_pnl": round(baseline_summary["total_pnl"], 3),
            "profit_factor": None if not math.isfinite(baseline_summary["profit_factor"]) else round(baseline_summary["profit_factor"], 2),
            "mdd_pct": round(baseline_summary["mdd_pct"], 3),
        },
        "score": 0.0,
        "delta_total_pnl": 0.0,
    }

    candidates: list[dict[str, Any]] = [baseline_candidate]
    for (
        tp,
        sl,
        activation,
        early,
        mid,
        late,
        max_hold,
        t5m_loss_exit_enabled,
    ) in product(
        (1.5, 2.0, 2.5, 3.0),
        (-1.0, -1.5, -2.0),
        (0.5, 0.8),
        (0.4, 0.5, 0.6),
        (0.6, 0.8, 1.0),
        (0.8, 1.0, 1.2),
        (10, 15, 20, 30),
        (True, False),
    ):
        if not (early <= mid <= late):
            continue
        candidate = ExitSimulationConfig(
            paper_take_profit_pct=tp,
            paper_stop_loss_pct=sl,
            trailing_stop_enabled=True,
            trailing_stop_activation_pct=activation,
            trailing_stop_early_pct=early,
            trailing_stop_mid_pct=mid,
            trailing_stop_late_pct=late,
            max_hold_minutes=max_hold,
            t5m_loss_exit_enabled=t5m_loss_exit_enabled,
            t5m_profit_trailing_pct=baseline_config.t5m_profit_trailing_pct,
            session_early_sl_multiplier=baseline_config.session_early_sl_multiplier,
            session_late_max_hold_divisor=baseline_config.session_late_max_hold_divisor,
        )
        ordered_pnls: list[float] = []
        for trade in sorted(trades, key=lambda item: item.detected_at):
            result = simulate_trade_exit(trade, candidate)
            ordered_pnls.append(result.exit_pnl_pct)
        summary = _summarize_pnls(ordered_pnls, ordered_pnls)
        score = (
            (summary["total_pnl"] - baseline_summary["total_pnl"]) * 2.0
            + summary["avg_pnl"] * 18.0
            + (summary["win_rate"] - baseline_summary["win_rate"]) / 10.0
            + min(4.0, summary["profit_factor"] if math.isfinite(summary["profit_factor"]) else 4.0)
            - abs(summary["mdd_pct"]) / 4.0
        )
        candidates.append(
            {
                "params": candidate.to_dict(),
                "count": summary["count"],
                "win_rate": round(summary["win_rate"], 1),
                "avg_pnl": round(summary["avg_pnl"], 3),
                "total_pnl": round(summary["total_pnl"], 3),
                "profit_factor": None if not math.isfinite(summary["profit_factor"]) else round(summary["profit_factor"], 2),
                "mdd_pct": round(summary["mdd_pct"], 3),
                "score": round(score, 3),
                "delta_total_pnl": round(summary["total_pnl"] - baseline_summary["total_pnl"], 3),
            }
        )

    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in candidates:
        params = row["params"]
        key = (
            params["paper_take_profit_pct"],
            params["paper_stop_loss_pct"],
            params["trailing_stop_activation_pct"],
            params["trailing_stop_early_pct"],
            params["trailing_stop_mid_pct"],
            params["trailing_stop_late_pct"],
            params["max_hold_minutes"],
            params["t5m_loss_exit_enabled"],
        )
        existing = deduped.get(key)
        if existing is None or row["score"] > existing["score"]:
            deduped[key] = row
    ranked = sorted(deduped.values(), key=lambda row: (row["score"], row["total_pnl"]), reverse=True)
    return baseline_candidate, ranked[:10]


def analyze_trades(
    trades: list[Trade],
    *,
    guardrail_blocks: list[GuardrailBlockRecord] | None = None,
    shadow_trades: list[Trade] | None = None,
    log_paths: Iterable[Path] | None = None,
    runtime_defaults: ExitSimulationConfig | None = None,
) -> dict[str, Any]:
    if not trades:
        return {"total_trades": 0, "message": "No trades found"}

    runtime_defaults = runtime_defaults or ExitSimulationConfig.from_runtime_defaults()
    runtime_cfg = Config()
    guardrail_blocks = guardrail_blocks or []
    shadow_trades = shadow_trades or []
    baseline = summarize_trade_group(trades)
    by_date = _group_stats(trades, lambda trade: trade.date)
    by_bucket = _group_stats(trades, lambda trade: trade.bucket or "unknown")
    by_hour = _group_stats(trades, lambda trade: f"{trade.hour:02d}" if trade.hour >= 0 else "unknown")
    by_hour_bucket = _group_stats(trades, lambda trade: trade.hour_bucket)
    by_source = _group_stats(trades, lambda trade: trade.decision_source or "unknown")
    by_news_type = _group_stats(trades, lambda trade: trade.news_type)
    by_ticker = _group_stats(trades, lambda trade: trade.ticker or "unknown")
    by_confidence = _group_stats(trades, lambda trade: _confidence_band(trade.confidence))
    by_exit_type = _group_stats(trades, lambda trade: trade.exit_type)
    by_news_type_hour_bucket = _nested_group_stats(trades, lambda trade: trade.news_type, lambda trade: trade.hour_bucket)

    horizon_returns: dict[str, Any] = {}
    for horizon in HORIZON_ORDER:
        values = [trade.snapshots[horizon] for trade in trades if horizon in trade.snapshots]
        if values:
            horizon_returns[horizon] = {
                "count": len(values),
                "avg": round(sum(values) / len(values), 3),
                "median": round(_median(values), 3),
                "win_rate": round(len([value for value in values if value > 0]) / len(values) * 100, 1),
            }

    profit_leakage = []
    for trade in trades:
        leaked = trade.max_gain_pct - trade.exit_pnl_pct
        if leaked <= 0:
            continue
        profit_leakage.append(
            {
                "ticker": trade.ticker,
                "headline": trade.headline[:50],
                "max_gain": round(trade.max_gain_pct, 2),
                "exit_pnl": round(trade.exit_pnl_pct, 2),
                "leaked": round(leaked, 2),
                "exit_type": trade.exit_type,
            }
        )

    entry_conditions = rank_entry_conditions(trades, baseline)
    baseline_exit, exit_candidates = _evaluate_exit_candidates(trades, runtime_defaults)
    shadow_summary = _stats_dict(shadow_trades) if shadow_trades else None
    guardrail_review = build_guardrail_review(trades, guardrail_blocks, shadow_trades)

    return {
        "analysis_window": {
            "log_count": len(list(log_paths or [])),
            "dates": sorted({trade.date for trade in trades}),
        },
        "runtime_defaults": {
            **runtime_defaults.to_dict(),
            "min_buy_confidence": runtime_cfg.min_buy_confidence,
            "opening_min_confidence": runtime_cfg.opening_min_confidence,
            "afternoon_min_confidence": runtime_cfg.afternoon_min_confidence,
            "closing_min_confidence": runtime_cfg.closing_min_confidence,
            "fast_profile_hold_minutes": runtime_cfg.fast_profile_hold_minutes,
            "fast_profile_no_buy_after_kst_hour": runtime_cfg.fast_profile_no_buy_after_kst_hour,
            "fast_profile_no_buy_after_kst_minute": runtime_cfg.fast_profile_no_buy_after_kst_minute,
            "dynamic_guardrails_enabled": runtime_cfg.dynamic_guardrails_enabled,
            "dynamic_guardrail_supportive_index_change_pct": runtime_cfg.dynamic_guardrail_supportive_index_change_pct,
            "dynamic_guardrail_supportive_breadth_ratio": runtime_cfg.dynamic_guardrail_supportive_breadth_ratio,
            "dynamic_guardrail_confidence_relaxation": runtime_cfg.dynamic_guardrail_confidence_relaxation,
            "dynamic_fast_profile_extension_minutes": runtime_cfg.dynamic_fast_profile_extension_minutes,
        },
        "total_trades": baseline["count"],
        "wins": baseline["wins"],
        "losses": baseline["losses"],
        "win_rate": round(baseline["win_rate"], 1),
        "avg_pnl": round(baseline["avg_pnl"], 3),
        "total_pnl_pct": round(baseline["total_pnl"], 3),
        "avg_win": round(baseline["avg_win"], 3),
        "avg_loss": round(baseline["avg_loss"], 3),
        "profit_factor": round(baseline["profit_factor"], 2) if math.isfinite(baseline["profit_factor"]) else None,
        "mdd_pct": round(baseline["mdd_pct"], 3),
        "max_single_gain": round(baseline["max_single_gain"], 3),
        "max_single_loss": round(baseline["max_single_loss"], 3),
        "by_confidence": by_confidence,
        "by_bucket": by_bucket,
        "by_hour": by_hour,
        "by_hour_bucket": by_hour_bucket,
        "by_exit_type": by_exit_type,
        "by_source": by_source,
        "by_date": by_date,
        "by_news_type": by_news_type,
        "by_ticker": by_ticker,
        "horizon_returns": horizon_returns,
        "profit_leakage": sorted(profit_leakage, key=lambda row: row["leaked"], reverse=True)[:10],
        "matrices": {
            "by_ticker": by_ticker,
            "by_hour": by_hour,
            "by_hour_bucket": by_hour_bucket,
            "by_news_type": by_news_type,
            "by_news_type_hour_bucket": by_news_type_hour_bucket,
        },
        "condition_scores": {
            "entry": entry_conditions[:15],
            "exit": {
                "baseline": baseline_exit,
                "candidates": exit_candidates,
            },
        },
        "recommended_conditions": {
            "entry": [row for row in entry_conditions if row["score"] > 0][:5],
            "exit": exit_candidates[0] if exit_candidates else baseline_exit,
        },
        "guardrail_review": guardrail_review,
        "shadow_summary": shadow_summary,
        "trade_rows": [trade.to_row() for trade in sorted(trades, key=lambda item: item.detected_at)],
    }


def analyze_paths(
    paths: list[Path],
    *,
    snapshot_dir: Path | None = None,
    runtime_defaults: ExitSimulationConfig | None = None,
) -> tuple[dict[str, Any], list[Trade], list[Trade]]:
    runtime_defaults = runtime_defaults or ExitSimulationConfig.from_runtime_defaults()
    trades: list[Trade] = []
    shadow_trades: list[Trade] = []
    guardrail_blocks: list[GuardrailBlockRecord] = []
    for path in paths:
        if not path.exists():
            print(f"  SKIP (not found): {path}", file=sys.stderr)
            continue
        date_str = path.stem.replace("kindshot_", "")
        events, decisions, snapshots = load_day(path)
        _append_runtime_snapshots(date_str, snapshots, snapshot_dir)
        day_trades = build_trades(events, decisions, snapshots, date_str, runtime_defaults)
        day_shadow = build_shadow_trades(events, snapshots, date_str, runtime_defaults)
        shadow_source_ids = {trade.event_id.replace("shadow_", "", 1) for trade in day_shadow}
        day_blocks = build_guardrail_blocks(events, date_str, shadow_source_ids=shadow_source_ids)
        print(
            f"  {date_str}: {len(day_trades)} executed BUY, {len(day_blocks)} blocked BUY, {len(day_shadow)} shadow trades",
            file=sys.stderr,
        )
        trades.extend(day_trades)
        shadow_trades.extend(day_shadow)
        guardrail_blocks.extend(day_blocks)
    stats = analyze_trades(
        trades,
        guardrail_blocks=guardrail_blocks,
        shadow_trades=shadow_trades,
        log_paths=paths,
        runtime_defaults=runtime_defaults,
    )
    return stats, trades, shadow_trades


def render_report(stats: dict[str, Any], trades: list[Trade]) -> str:
    if stats.get("total_trades", 0) == 0:
        return "No trades found."

    lines: list[str] = []
    w = lines.append

    w("=" * 78)
    w("  KINDSHOT DEEP BACKTEST ANALYSIS")
    w("=" * 78)
    w("")

    w("## 1. Overall Performance")
    w(f"  Logs analyzed: {stats['analysis_window']['log_count']}")
    w(f"  Dates: {', '.join(stats['analysis_window']['dates'])}")
    w(f"  Total trades: {stats['total_trades']}")
    w(f"  Win rate: {stats['win_rate']}% ({stats['wins']}W / {stats['losses']}L)")
    w(f"  Avg PnL: {stats['avg_pnl']:+.3f}%")
    w(f"  Total PnL: {stats['total_pnl_pct']:+.3f}%")
    w(f"  Avg win / loss: {stats['avg_win']:+.3f}% / {stats['avg_loss']:+.3f}%")
    w(f"  Profit factor: {stats['profit_factor']}")
    w(f"  MDD: {stats['mdd_pct']:+.3f}%")
    w("")

    guardrail_review = stats.get("guardrail_review", {})
    if guardrail_review:
        w("## 2. Guardrail Review")
        w(
            f"  Inline BUY total: {guardrail_review['inline_buy_total']} | "
            f"passed={guardrail_review['passed_buy_count']} blocked={guardrail_review['blocked_buy_count']} "
            f"(block_rate={guardrail_review['block_rate_pct']}%)"
        )
        w(
            f"  Replay coverage: passed={guardrail_review['replayed_passed_buy_count']} trades | "
            f"blocked shadow={guardrail_review['shadow_blocked_buy_count']} "
            f"({guardrail_review['shadow_coverage_pct']}% of blocked BUYs)"
        )
        blocked_shadow = guardrail_review.get("blocked_shadow_summary")
        if blocked_shadow:
            w(
                f"  Shadow blocked avg={blocked_shadow['avg_pnl']:+.3f}% "
                f"win={blocked_shadow['win_rate']}% total={blocked_shadow['total_pnl']:+.3f}%"
            )
        w("")

        w("## 3. Blockers By Reason")
        w(f"  {'Reason':<28} {'Cnt':>4} {'Share':>7} {'Shadow':>7} {'ShadowAvg':>10}")
        w(f"  {'-' * 28} {'-' * 4} {'-' * 7} {'-' * 7} {'-' * 10}")
        for reason, row in guardrail_review["by_reason"].items():
            shadow_summary = row["shadow_summary"]
            shadow_avg = "-" if shadow_summary is None else f"{shadow_summary['avg_pnl']:+.3f}%"
            w(
                f"  {reason[:28]:<28} {row['count']:>4} {row['share_pct']:>6.1f}% "
                f"{row['shadow_count']:>7} {shadow_avg:>10}"
            )
        w("")

        def write_count_group(title: str, rows: dict[str, int]) -> None:
            w(title)
            w(f"  {'Label':<24} {'Cnt':>4}")
            w(f"  {'-' * 24} {'-' * 4}")
            for label, count in rows.items():
                w(f"  {label:<24} {count:>4}")
            w("")

        write_count_group("## 4. Blockers By Confidence Band", guardrail_review["by_confidence_band"])
        write_count_group("## 5. Blockers By Hour Bucket", guardrail_review["by_hour_bucket"])

    def write_group(title: str, rows: dict[str, Any], limit: int | None = None) -> None:
        w(title)
        w(f"  {'Label':<24} {'Cnt':>4} {'Win%':>7} {'Avg':>8} {'Total':>9} {'PF':>6}")
        w(f"  {'-' * 24} {'-' * 4} {'-' * 7} {'-' * 8} {'-' * 9} {'-' * 6}")
        ordered = list(rows.items())
        ordered.sort(key=lambda item: (-item[1]['count'], item[0]))
        if limit is not None:
            ordered = ordered[:limit]
        for label, row in ordered:
            pf = "-" if row["profit_factor"] is None else f"{row['profit_factor']:.2f}"
            w(
                f"  {label:<24} {row['count']:>4} {row['win_rate']:>6.1f}% "
                f"{row['avg_pnl']:>+7.3f}% {row['total_pnl']:>+8.3f}% {pf:>6}"
            )
        w("")

    write_group("## 6. By News Type", stats["by_news_type"])
    write_group("## 7. By Hour", stats["by_hour"])
    write_group("## 8. By Hour Bucket", stats["by_hour_bucket"])
    write_group("## 9. By Confidence Band", stats["by_confidence"])
    write_group("## 10. By Ticker (Top 10 by count)", stats["by_ticker"], limit=10)

    w("## 11. Top Entry Conditions")
    w(f"  {'Category':<24} {'Label':<26} {'Cnt':>4} {'Win%':>7} {'Avg':>8} {'Score':>7}")
    w(f"  {'-' * 24} {'-' * 26} {'-' * 4} {'-' * 7} {'-' * 8} {'-' * 7}")
    for row in stats["condition_scores"]["entry"][:10]:
        w(
            f"  {row['category']:<24} {row['label'][:26]:<26} {row['count']:>4} "
            f"{row['win_rate']:>6.1f}% {row['avg_pnl']:>+7.3f}% {row['score']:>7.3f}"
        )
    w("")

    w("## 12. Exit Optimization Candidates")
    w(f"  {'TP':>4} {'SL':>5} {'Act':>5} {'Hold':>5} {'T5M':>5} {'Win%':>7} {'Avg':>8} {'Total':>9} {'Score':>7}")
    w(f"  {'-' * 4} {'-' * 5} {'-' * 5} {'-' * 5} {'-' * 5} {'-' * 7} {'-' * 8} {'-' * 9} {'-' * 7}")
    for row in stats["condition_scores"]["exit"]["candidates"][:5]:
        params = row["params"]
        w(
            f"  {params['paper_take_profit_pct']:>4.1f} {params['paper_stop_loss_pct']:>+5.1f} "
            f"{params['trailing_stop_activation_pct']:>5.1f} {params['max_hold_minutes']:>5} "
            f"{str(params['t5m_loss_exit_enabled']):>5} {row['win_rate']:>6.1f}% "
            f"{row['avg_pnl']:>+7.3f}% {row['total_pnl']:>+8.3f}% {row['score']:>7.3f}"
        )
    w("")

    write_group("## 13. By Exit Type", stats["by_exit_type"])

    w("## 14. Horizon Returns")
    w(f"  {'Horizon':<10} {'Cnt':>4} {'Win%':>7} {'Avg':>8} {'Median':>8}")
    w(f"  {'-' * 10} {'-' * 4} {'-' * 7} {'-' * 8} {'-' * 8}")
    for horizon in HORIZON_ORDER:
        row = stats["horizon_returns"].get(horizon)
        if not row:
            continue
        w(
            f"  {horizon:<10} {row['count']:>4} {row['win_rate']:>6.1f}% "
            f"{row['avg']:>+7.3f}% {row['median']:>+7.3f}%"
        )
    w("")

    if stats["profit_leakage"]:
        w("## 15. Top Profit Leakage")
        w(f"  {'Ticker':<8} {'Leak':>7} {'Exit':<12} {'Headline'}")
        w(f"  {'-' * 8} {'-' * 7} {'-' * 12} {'-' * 40}")
        for row in stats["profit_leakage"]:
            w(f"  {row['ticker']:<8} {row['leaked']:>+6.2f}% {row['exit_type']:<12} {row['headline']}")
        w("")

    w("## 16. Trades Detail")
    w(f"  {'Date':<10} {'Ticker':<8} {'NewsType':<22} {'Hour':>4} {'Conf':>4} {'PnL':>8} {'Exit':<12} {'Headline'}")
    w(f"  {'-' * 10} {'-' * 8} {'-' * 22} {'-' * 4} {'-' * 4} {'-' * 8} {'-' * 12} {'-' * 40}")
    for trade in sorted(trades, key=lambda item: item.detected_at):
        w(
            f"  {trade.date:<10} {trade.ticker:<8} {trade.news_type[:22]:<22} {trade.hour:>4} "
            f"{trade.confidence:>4} {trade.exit_pnl_pct:>+7.2f}% {trade.exit_type:<12} {trade.headline[:40]}"
        )

    shadow_summary = stats.get("shadow_summary")
    if shadow_summary:
        w("")
        w("## 17. Shadow Summary")
        w(
            f"  Shadow trades: {shadow_summary['count']} | win={shadow_summary['win_rate']}% "
            f"| avg={shadow_summary['avg_pnl']:+.3f}% | total={shadow_summary['total_pnl']:+.3f}%"
        )

    return "\n".join(lines)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _resolve_output_paths(output: Path, fmt: str) -> tuple[Path | None, Path | None]:
    if fmt == "text":
        return output, None
    if fmt == "json":
        return None, output
    if output.suffix == ".json":
        return output.with_suffix(".txt"), output
    return output, output.with_suffix(".json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="logs", help="Directory containing JSONL logs")
    parser.add_argument("--dates", nargs="*", help="Specific dates (YYYYMMDD) to analyze")
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--format", choices=("text", "json", "both"), default="text")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    if args.dates:
        paths = [log_dir / f"kindshot_{date_str}.jsonl" for date_str in args.dates]
    else:
        paths = sorted(log_dir.glob("kindshot_*.jsonl"))

    snapshot_dir = PROJECT_ROOT / "data" / "runtime" / "price_snapshots"
    runtime_defaults = ExitSimulationConfig.from_runtime_defaults()
    stats, trades, _shadow_trades = analyze_paths(paths, snapshot_dir=snapshot_dir, runtime_defaults=runtime_defaults)

    if stats.get("total_trades", 0) == 0:
        print("No trades found.", file=sys.stderr)
        return

    report = render_report(stats, trades)
    json_payload = json.dumps(stats, indent=2, ensure_ascii=False, default=_json_default)

    if args.output:
        text_path, json_path = _resolve_output_paths(Path(args.output), args.format)
        if text_path is not None:
            text_path.write_text(report, encoding="utf-8")
            print(f"Text report saved to {text_path}", file=sys.stderr)
        if json_path is not None:
            json_path.write_text(json_payload, encoding="utf-8")
            print(f"JSON report saved to {json_path}", file=sys.stderr)
    else:
        if args.format == "text":
            print(report)
        elif args.format == "json":
            print(json_payload)
        else:
            print(report)
            print("")
            print(json_payload)


if __name__ == "__main__":
    main()
