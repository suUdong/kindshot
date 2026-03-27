#!/usr/bin/env python3
"""Comprehensive backtest analysis for kindshot trading logs.

Analyzes BUY trades across multiple days:
- Win rate, total P&L, MDD
- Breakdown by confidence, bucket, time-of-day, decision_source
- v65 effect measurement (pre/post comparison)
- Exit analysis (TP/SL/trailing/stale)
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kindshot.tz import KST as _KST


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
    entry_price: float = 0.0
    snapshots: dict[str, float] = field(default_factory=dict)
    exit_type: str = ""  # TP, SL, TRAILING, STALE, CLOSE
    exit_pnl_pct: float = 0.0
    max_gain_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    hold_minutes: float = 0.0


def _parse_kst_hour(ts: str) -> int:
    if not ts:
        return -1
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_KST)
        else:
            dt = dt.astimezone(_KST)
        return dt.hour
    except (ValueError, TypeError):
        return -1


def load_day(path: Path) -> tuple[list[dict], list[dict], list[dict]]:
    """Load events, decisions, and price_snapshots from a JSONL log."""
    events, decisions, snapshots = [], [], []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = rec.get("type", rec.get("record_type", ""))
            if t == "event":
                events.append(rec)
            elif t == "decision":
                decisions.append(rec)
            elif t == "price_snapshot":
                snapshots.append(rec)
    return events, decisions, snapshots


def build_trades(
    events: list[dict],
    decisions: list[dict],
    snapshots: list[dict],
    date_str: str,
) -> list[Trade]:
    """Build Trade objects from log records."""
    # Index decisions by event_id
    decision_map: dict[str, dict] = {}
    for d in decisions:
        if d.get("action") == "BUY":
            decision_map[d["event_id"]] = d

    # Index snapshots by event_id
    snap_map: dict[str, dict[str, float]] = defaultdict(dict)
    for s in snapshots:
        eid = s.get("event_id", "")
        horizon = s.get("horizon", "")
        px = s.get("px")
        if eid and horizon and px is not None:
            snap_map[eid][horizon] = float(px)

    trades: list[Trade] = []
    for ev in events:
        eid = ev.get("event_id", "")
        action = ev.get("decision_action")
        skip_stage = ev.get("skip_stage")
        guardrail_result = ev.get("guardrail_result")

        # Only count executed BUYs (passed guardrails)
        if action != "BUY":
            continue
        if skip_stage and skip_stage not in ("None", ""):
            continue

        dec = decision_map.get(eid, {})
        snaps = snap_map.get(eid, {})
        t0 = snaps.get("t0", 0.0)

        if t0 <= 0:
            continue

        # Calculate returns at each horizon
        returns = {}
        max_gain = 0.0
        max_dd = 0.0
        for h in ["t+30s", "t+1m", "t+2m", "t+5m", "t+10m", "t+15m", "t+20m", "t+30m", "close"]:
            px = snaps.get(h)
            if px and px > 0:
                ret = (px - t0) / t0 * 100
                returns[h] = ret
                max_gain = max(max_gain, ret)
                max_dd = min(max_dd, ret)

        # Determine exit type and P&L
        # Use the latest available snapshot as the exit
        exit_pnl = 0.0
        exit_type = "NO_DATA"
        hold_min = 0.0

        # Check exit horizons in order
        horizon_minutes = {
            "t+30s": 0.5, "t+1m": 1, "t+2m": 2, "t+5m": 5,
            "t+10m": 10, "t+15m": 15, "t+20m": 20, "t+30m": 30, "close": 999
        }

        for h, mins in horizon_minutes.items():
            if h in returns:
                exit_pnl = returns[h]
                hold_min = mins

        # Classify exit type based on P&L pattern
        if exit_pnl >= 1.5:
            exit_type = "TP"
        elif exit_pnl <= -1.0:
            exit_type = "SL"
        elif max_gain >= 0.5 and exit_pnl < max_gain * 0.5:
            exit_type = "TRAILING"
        elif abs(exit_pnl) < 0.3:
            exit_type = "STALE"
        else:
            exit_type = "CLOSE"

        trade = Trade(
            event_id=eid,
            date=date_str,
            ticker=ev.get("ticker", ""),
            headline=ev.get("headline", "")[:60],
            bucket=ev.get("bucket", ""),
            confidence=int(dec.get("confidence", ev.get("decision_confidence", 0))),
            size_hint=dec.get("size_hint", ev.get("decision_size_hint", "M")),
            reason=dec.get("reason", "")[:60],
            decision_source=dec.get("decision_source", ""),
            detected_at=ev.get("detected_at", ""),
            entry_price=t0,
            snapshots=returns,
            exit_type=exit_type,
            exit_pnl_pct=exit_pnl,
            max_gain_pct=max_gain,
            max_drawdown_pct=max_dd,
            hold_minutes=hold_min,
        )
        trades.append(trade)

    return trades


def analyze_trades(trades: list[Trade]) -> dict[str, Any]:
    """Compute aggregate statistics from trades."""
    if not trades:
        return {"total": 0, "message": "No trades found"}

    total = len(trades)
    wins = [t for t in trades if t.exit_pnl_pct > 0]
    losses = [t for t in trades if t.exit_pnl_pct <= 0]
    win_rate = len(wins) / total * 100

    pnls = [t.exit_pnl_pct for t in trades]
    avg_pnl = sum(pnls) / total
    total_pnl = sum(pnls)
    avg_win = sum(t.exit_pnl_pct for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.exit_pnl_pct for t in losses) / len(losses) if losses else 0

    # MDD (sequential)
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for t in sorted(trades, key=lambda x: x.detected_at):
        cum += t.exit_pnl_pct
        peak = max(peak, cum)
        dd = cum - peak
        mdd = min(mdd, dd)

    # By confidence band
    conf_bands = {"75-77": [], "78-80": [], "81-85": [], "86-90": [], "91+": []}
    for t in trades:
        c = t.confidence
        if c <= 77:
            conf_bands["75-77"].append(t)
        elif c <= 80:
            conf_bands["78-80"].append(t)
        elif c <= 85:
            conf_bands["81-85"].append(t)
        elif c <= 90:
            conf_bands["86-90"].append(t)
        else:
            conf_bands["91+"].append(t)

    conf_stats = {}
    for band, tlist in conf_bands.items():
        if tlist:
            w = len([t for t in tlist if t.exit_pnl_pct > 0])
            conf_stats[band] = {
                "count": len(tlist),
                "win_rate": w / len(tlist) * 100,
                "avg_pnl": sum(t.exit_pnl_pct for t in tlist) / len(tlist),
                "total_pnl": sum(t.exit_pnl_pct for t in tlist),
            }

    # By bucket
    bucket_stats = {}
    for bucket in set(t.bucket for t in trades):
        tlist = [t for t in trades if t.bucket == bucket]
        w = len([t for t in tlist if t.exit_pnl_pct > 0])
        bucket_stats[bucket] = {
            "count": len(tlist),
            "win_rate": w / len(tlist) * 100,
            "avg_pnl": sum(t.exit_pnl_pct for t in tlist) / len(tlist),
        }

    # By hour (KST)
    hour_stats: dict[int, list[Trade]] = defaultdict(list)
    for t in trades:
        h = _parse_kst_hour(t.detected_at)
        hour_stats[h].append(t)

    hour_result = {}
    for h in sorted(hour_stats):
        tlist = hour_stats[h]
        w = len([t for t in tlist if t.exit_pnl_pct > 0])
        hour_result[f"{h:02d}"] = {
            "count": len(tlist),
            "win_rate": w / len(tlist) * 100,
            "avg_pnl": sum(t.exit_pnl_pct for t in tlist) / len(tlist),
        }

    # By exit type
    exit_stats: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        exit_stats[t.exit_type].append(t)

    exit_result = {}
    for et, tlist in exit_stats.items():
        exit_result[et] = {
            "count": len(tlist),
            "avg_pnl": sum(t.exit_pnl_pct for t in tlist) / len(tlist),
        }

    # By decision source
    source_stats: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        source_stats[t.decision_source or "unknown"].append(t)

    source_result = {}
    for src, tlist in source_stats.items():
        w = len([t for t in tlist if t.exit_pnl_pct > 0])
        source_result[src] = {
            "count": len(tlist),
            "win_rate": w / len(tlist) * 100,
            "avg_pnl": sum(t.exit_pnl_pct for t in tlist) / len(tlist),
        }

    # By date
    date_stats: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        date_stats[t.date].append(t)

    daily_result = {}
    for d in sorted(date_stats):
        tlist = date_stats[d]
        w = len([t for t in tlist if t.exit_pnl_pct > 0])
        daily_result[d] = {
            "count": len(tlist),
            "win_rate": w / len(tlist) * 100,
            "total_pnl": sum(t.exit_pnl_pct for t in tlist),
            "avg_pnl": sum(t.exit_pnl_pct for t in tlist) / len(tlist),
        }

    # Horizon analysis: avg return at each snapshot
    horizons = ["t+30s", "t+1m", "t+2m", "t+5m", "t+10m", "t+15m", "t+20m", "t+30m", "close"]
    horizon_avg = {}
    for h in horizons:
        rets = [t.snapshots.get(h) for t in trades if h in t.snapshots]
        if rets:
            horizon_avg[h] = {
                "count": len(rets),
                "avg": sum(rets) / len(rets),
                "median": sorted(rets)[len(rets) // 2],
                "win_rate": len([r for r in rets if r > 0]) / len(rets) * 100,
            }

    # Max gain vs exit P&L (profit leakage)
    profit_leakage = []
    for t in trades:
        if t.max_gain_pct > 0.5:
            leak = t.max_gain_pct - t.exit_pnl_pct
            profit_leakage.append({
                "ticker": t.ticker,
                "headline": t.headline[:40],
                "max_gain": round(t.max_gain_pct, 2),
                "exit_pnl": round(t.exit_pnl_pct, 2),
                "leaked": round(leak, 2),
                "exit_type": t.exit_type,
            })

    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_pnl": round(avg_pnl, 3),
        "total_pnl_pct": round(total_pnl, 2),
        "avg_win": round(avg_win, 3),
        "avg_loss": round(avg_loss, 3),
        "profit_factor": round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else float("inf"),
        "mdd_pct": round(mdd, 2),
        "max_single_gain": round(max(t.max_gain_pct for t in trades), 2),
        "max_single_loss": round(min(t.max_drawdown_pct for t in trades), 2),
        "by_confidence": conf_stats,
        "by_bucket": bucket_stats,
        "by_hour": hour_result,
        "by_exit_type": exit_result,
        "by_source": source_result,
        "by_date": daily_result,
        "horizon_returns": horizon_avg,
        "profit_leakage": sorted(profit_leakage, key=lambda x: -x["leaked"])[:10],
    }


def render_report(stats: dict[str, Any], trades: list[Trade]) -> str:
    lines: list[str] = []
    w = lines.append

    w("=" * 70)
    w("  KINDSHOT BACKTEST ANALYSIS REPORT")
    w("=" * 70)
    w("")

    # Summary
    w("## 1. Overall Performance")
    w(f"  총 트레이드: {stats['total_trades']}건")
    w(f"  승률: {stats['win_rate']}% ({stats['wins']}W / {stats['losses']}L)")
    w(f"  평균 P&L: {stats['avg_pnl']:.3f}%")
    w(f"  누적 P&L: {stats['total_pnl_pct']:.2f}%")
    w(f"  평균 승: +{stats['avg_win']:.3f}% | 평균 패: {stats['avg_loss']:.3f}%")
    w(f"  Profit Factor: {stats['profit_factor']}")
    w(f"  MDD: {stats['mdd_pct']:.2f}%")
    w(f"  최대 단일 수익: +{stats['max_single_gain']:.2f}%")
    w(f"  최대 단일 손실: {stats['max_single_loss']:.2f}%")
    w("")

    # Daily breakdown
    w("## 2. Daily Breakdown")
    w(f"  {'Date':<12} {'Trades':>6} {'WinRate':>8} {'TotalPnL':>10} {'AvgPnL':>8}")
    w(f"  {'-'*12} {'-'*6} {'-'*8} {'-'*10} {'-'*8}")
    for d, ds in stats["by_date"].items():
        w(f"  {d:<12} {ds['count']:>6} {ds['win_rate']:>7.1f}% {ds['total_pnl']:>+9.2f}% {ds['avg_pnl']:>+7.3f}%")
    w("")

    # Confidence breakdown
    w("## 3. By Confidence Band")
    w(f"  {'Band':<8} {'Count':>6} {'WinRate':>8} {'AvgPnL':>8} {'TotalPnL':>10}")
    w(f"  {'-'*8} {'-'*6} {'-'*8} {'-'*8} {'-'*10}")
    for band, cs in stats["by_confidence"].items():
        w(f"  {band:<8} {cs['count']:>6} {cs['win_rate']:>7.1f}% {cs['avg_pnl']:>+7.3f}% {cs['total_pnl']:>+9.2f}%")
    w("")

    # Bucket breakdown
    w("## 4. By Bucket")
    w(f"  {'Bucket':<15} {'Count':>6} {'WinRate':>8} {'AvgPnL':>8}")
    w(f"  {'-'*15} {'-'*6} {'-'*8} {'-'*8}")
    for bucket, bs in sorted(stats["by_bucket"].items()):
        w(f"  {bucket:<15} {bs['count']:>6} {bs['win_rate']:>7.1f}% {bs['avg_pnl']:>+7.3f}%")
    w("")

    # Hour breakdown
    w("## 5. By Hour (KST)")
    w(f"  {'Hour':<6} {'Count':>6} {'WinRate':>8} {'AvgPnL':>8}")
    w(f"  {'-'*6} {'-'*6} {'-'*8} {'-'*8}")
    for h, hs in stats["by_hour"].items():
        w(f"  {h}:00  {hs['count']:>6} {hs['win_rate']:>7.1f}% {hs['avg_pnl']:>+7.3f}%")
    w("")

    # Exit type
    w("## 6. By Exit Type")
    w(f"  {'Type':<12} {'Count':>6} {'AvgPnL':>8}")
    w(f"  {'-'*12} {'-'*6} {'-'*8}")
    for et, es in sorted(stats["by_exit_type"].items()):
        w(f"  {et:<12} {es['count']:>6} {es['avg_pnl']:>+7.3f}%")
    w("")

    # Decision source
    w("## 7. By Decision Source")
    w(f"  {'Source':<18} {'Count':>6} {'WinRate':>8} {'AvgPnL':>8}")
    w(f"  {'-'*18} {'-'*6} {'-'*8} {'-'*8}")
    for src, ss in sorted(stats["by_source"].items()):
        w(f"  {src:<18} {ss['count']:>6} {ss['win_rate']:>7.1f}% {ss['avg_pnl']:>+7.3f}%")
    w("")

    # Horizon returns
    w("## 8. Average Return by Horizon")
    w(f"  {'Horizon':<10} {'N':>4} {'AvgRet':>8} {'Median':>8} {'WinRate':>8}")
    w(f"  {'-'*10} {'-'*4} {'-'*8} {'-'*8} {'-'*8}")
    for h, hr in stats["horizon_returns"].items():
        w(f"  {h:<10} {hr['count']:>4} {hr['avg']:>+7.3f}% {hr['median']:>+7.3f}% {hr['win_rate']:>7.1f}%")
    w("")

    # Profit leakage
    if stats["profit_leakage"]:
        w("## 9. Top Profit Leakage (max_gain vs exit)")
        w(f"  {'Ticker':<8} {'MaxGain':>8} {'ExitPnL':>8} {'Leaked':>8} {'Exit':<10} {'Headline'}")
        w(f"  {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*10} {'-'*30}")
        for pl in stats["profit_leakage"]:
            w(f"  {pl['ticker']:<8} {pl['max_gain']:>+7.2f}% {pl['exit_pnl']:>+7.2f}% {pl['leaked']:>+7.2f}% {pl['exit_type']:<10} {pl['headline']}")
        w("")

    # Individual trades
    w("## 10. All Trades Detail")
    w(f"  {'Date':<10} {'Ticker':<8} {'Conf':>4} {'Size':>4} {'Entry':>10} {'PnL':>8} {'MaxG':>7} {'MaxDD':>7} {'Exit':<10} {'Headline'}")
    w(f"  {'-'*10} {'-'*8} {'-'*4} {'-'*4} {'-'*10} {'-'*8} {'-'*7} {'-'*7} {'-'*10} {'-'*30}")
    for t in sorted(trades, key=lambda x: x.detected_at):
        pnl_str = f"{t.exit_pnl_pct:>+7.2f}%"
        w(f"  {t.date:<10} {t.ticker:<8} {t.confidence:>4} {t.size_hint:>4} {t.entry_price:>10.0f} {pnl_str} {t.max_gain_pct:>+6.2f}% {t.max_drawdown_pct:>+6.2f}% {t.exit_type:<10} {t.headline[:35]}")
    w("")

    # Shadow snapshot 기회비용 분석 (v66)
    shadow_section = _render_shadow_section(trades, stats.get("_shadow_trades", []))
    if shadow_section:
        w("")
        w(shadow_section)

    return "\n".join(lines)


def build_shadow_trades(
    events: list[dict],
    snapshots: list[dict],
    date_str: str,
    tp_pct: float = 2.0,
    sl_pct: float = -1.5,
) -> list[Trade]:
    """shadow_ prefix 스냅샷에서 가상 트레이드 구성."""
    # shadow_ prefix 스냅샷만 필터링
    shadow_snaps: dict[str, dict[str, float]] = defaultdict(dict)
    for s in snapshots:
        eid = s.get("event_id", "")
        if not eid.startswith("shadow_"):
            continue
        horizon = s.get("horizon", "")
        px = s.get("px")
        if eid and horizon and px is not None:
            shadow_snaps[eid][horizon] = float(px)

    if not shadow_snaps:
        return []

    # 원본 event_id로 이벤트 정보 매칭
    event_map: dict[str, dict] = {}
    for ev in events:
        event_map[ev.get("event_id", "")] = ev

    trades: list[Trade] = []
    horizons = ["t+30s", "t+1m", "t+2m", "t+5m", "t+10m", "t+15m", "t+20m", "t+30m", "close"]

    for shadow_eid, snaps in shadow_snaps.items():
        t0 = snaps.get("t0", 0.0)
        if t0 <= 0:
            continue

        original_eid = shadow_eid.replace("shadow_", "", 1)
        ev = event_map.get(original_eid, {})

        returns: dict[str, float] = {}
        max_gain = 0.0
        max_dd = 0.0
        for h in horizons:
            px = snaps.get(h)
            if px and px > 0:
                ret = (px - t0) / t0 * 100
                returns[h] = ret
                max_gain = max(max_gain, ret)
                max_dd = min(max_dd, ret)

        # 가상 TP/SL 판정
        exit_type = "HOLD"
        exit_pnl = 0.0
        for h in horizons:
            if h not in returns:
                continue
            ret = returns[h]
            if ret >= tp_pct:
                exit_type = "TP"
                exit_pnl = ret
                break
            elif ret <= sl_pct:
                exit_type = "SL"
                exit_pnl = ret
                break
            exit_pnl = ret

        trade = Trade(
            event_id=shadow_eid,
            date=date_str,
            ticker=ev.get("ticker", "?"),
            headline=ev.get("headline", "")[:60],
            bucket=ev.get("bucket", "?"),
            confidence=int(ev.get("decision_confidence", 0)),
            size_hint=ev.get("decision_size_hint", "?"),
            reason=ev.get("skip_reason", ""),
            decision_source="SHADOW",
            detected_at=ev.get("detected_at", ""),
            entry_price=t0,
            snapshots=returns,
            exit_type=exit_type,
            exit_pnl_pct=exit_pnl,
            max_gain_pct=max_gain,
            max_drawdown_pct=max_dd,
        )
        trades.append(trade)

    return trades


def _render_shadow_section(real_trades: list[Trade], shadow_trades: list[Trade]) -> str:
    """Shadow 기회비용 분석 섹션 렌더링."""
    if not shadow_trades:
        return ""

    lines: list[str] = []
    w = lines.append

    total = len(shadow_trades)
    tp_trades = [t for t in shadow_trades if t.exit_type == "TP"]
    sl_trades = [t for t in shadow_trades if t.exit_type == "SL"]
    virtual_wr = len(tp_trades) / total * 100 if total else 0
    avg_pnl = sum(t.exit_pnl_pct for t in shadow_trades) / total if total else 0

    w("## 11. Shadow Snapshot 기회비용 분석 (차단된 BUY)")
    w(f"  차단된 BUY 시그널: {total}건")
    w(f"  가상 승률: {virtual_wr:.1f}% (TP: {len(tp_trades)}, SL: {len(sl_trades)}, HOLD: {total - len(tp_trades) - len(sl_trades)})")
    w(f"  가상 평균 P&L: {avg_pnl:+.3f}%")
    avg_max_gain = sum(t.max_gain_pct for t in shadow_trades) / total
    w(f"  평균 최대 수익: {avg_max_gain:+.3f}%")
    w("")
    w(f"  {'Date':<10} {'Ticker':<8} {'Conf':>4} {'PnL':>8} {'MaxG':>7} {'MaxDD':>7} {'Exit':<8} {'Reason'}")
    w(f"  {'-'*10} {'-'*8} {'-'*4} {'-'*8} {'-'*7} {'-'*7} {'-'*8} {'-'*20}")
    for t in shadow_trades:
        w(f"  {t.date:<10} {t.ticker:<8} {t.confidence:>4} {t.exit_pnl_pct:>+7.2f}% {t.max_gain_pct:>+6.2f}% {t.max_drawdown_pct:>+6.2f}% {t.exit_type:<8} {t.reason[:30]}")
    w("")

    # 실제 트레이드와 비교
    if real_trades:
        real_wr = len([t for t in real_trades if t.exit_pnl_pct > 0]) / len(real_trades) * 100
        real_avg = sum(t.exit_pnl_pct for t in real_trades) / len(real_trades)
        w(f"  비교: 실제 승률 {real_wr:.1f}% / 가상 승률 {virtual_wr:.1f}%")
        w(f"  비교: 실제 평균 P&L {real_avg:+.3f}% / 가상 평균 P&L {avg_pnl:+.3f}%")
        if virtual_wr > real_wr and avg_pnl > real_avg:
            w("  ⚠ WARNING: guardrail이 수익 기회를 과도하게 차단하고 있을 수 있음")
        elif virtual_wr < real_wr:
            w("  ✓ guardrail이 정상 작동 — 차단된 시그널이 실제보다 낮은 성과")

    return "\n".join(lines)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="logs", help="Directory containing JSONL logs")
    parser.add_argument("--dates", nargs="*", help="Specific dates (YYYYMMDD) to analyze")
    parser.add_argument("--output", help="Output file path")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    if args.dates:
        paths = [log_dir / f"kindshot_{d}.jsonl" for d in args.dates]
    else:
        paths = sorted(log_dir.glob("kindshot_*.jsonl"))

    all_trades: list[Trade] = []
    all_shadow_trades: list[Trade] = []
    snapshot_dir = PROJECT_ROOT / "data" / "runtime" / "price_snapshots"
    for path in paths:
        if not path.exists():
            print(f"  SKIP (not found): {path}", file=sys.stderr)
            continue
        date_str = path.stem.replace("kindshot_", "")
        events, decisions, snapshots = load_day(path)
        # snapshot 디렉토리에서 추가 snapshot 로드 (shadow 포함)
        snap_file = snapshot_dir / f"{date_str}.jsonl"
        if snap_file.exists():
            for line in snap_file.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "price_snapshot":
                    snapshots.append(rec)
        day_trades = build_trades(events, decisions, snapshots, date_str)
        day_shadow = build_shadow_trades(events, snapshots, date_str)
        print(f"  {date_str}: {len(day_trades)} executed BUY, {len(day_shadow)} shadow trades", file=sys.stderr)
        all_trades.extend(day_trades)
        all_shadow_trades.extend(day_shadow)

    if not all_trades and not all_shadow_trades:
        print("No trades found.", file=sys.stderr)
        return

    if all_trades:
        stats = analyze_trades(all_trades)
    else:
        stats = {"total": 0, "message": "No executed trades"}
    stats["_shadow_trades"] = all_shadow_trades
    report = render_report(stats, all_trades)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"\nReport saved to {args.output}", file=sys.stderr)
    else:
        print(report)

    # Also output JSON stats for programmatic use
    json_out = Path(args.output).with_suffix(".json") if args.output else None
    if json_out:
        # Round floats for readability
        json_out.write_text(json.dumps(stats, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"JSON stats saved to {json_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
