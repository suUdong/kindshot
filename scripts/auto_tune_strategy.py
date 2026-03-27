#!/usr/bin/env python3
"""Recommend Kindshot strategy parameters from backtest analysis JSON."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

_KST = timezone(timedelta(hours=9))


def load_analysis(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_kst(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_KST)
    return dt.astimezone(_KST)


def _stats_from_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {"count": 0.0, "win_rate": 0.0, "avg_pnl": 0.0, "total_pnl": 0.0}
    pnls = [float(row.get("exit_pnl_pct", 0.0)) for row in rows]
    wins = len([p for p in pnls if p > 0])
    return {
        "count": float(len(rows)),
        "win_rate": wins / len(rows) * 100.0,
        "avg_pnl": sum(pnls) / len(rows),
        "total_pnl": sum(pnls),
    }


def _score_threshold(rows: list[dict[str, Any]], threshold: int) -> dict[str, float] | None:
    filtered = [row for row in rows if int(row.get("confidence", 0)) >= threshold]
    if not filtered:
        return None
    stats = _stats_from_rows(filtered)
    sample_factor = min(1.0, stats["count"] / 4.0)
    stats["threshold"] = float(threshold)
    stats["score"] = sample_factor * (stats["avg_pnl"] * 25.0 + stats["win_rate"] / 10.0 + stats["total_pnl"])
    return stats


def _choose_threshold(
    rows: list[dict[str, Any]],
    *,
    current: int,
    minimum: int = 75,
    maximum: int = 90,
) -> tuple[int, dict[str, float] | None]:
    best = _score_threshold(rows, current)
    best_threshold = current
    for threshold in range(minimum, maximum + 1):
        stats = _score_threshold(rows, threshold)
        if stats is None:
            continue
        if best is None or stats["score"] > best["score"] + 0.05:
            best = stats
            best_threshold = threshold
    return best_threshold, best


def _period_filter(name: str) -> Callable[[dict[str, Any]], bool]:
    def match(row: dict[str, Any]) -> bool:
        dt = _parse_kst(str(row.get("detected_at", "")))
        if dt is None:
            return False
        if name == "opening":
            return (dt.hour == 8 and dt.minute >= 30) or (dt.hour == 9 and dt.minute < 30)
        if name == "afternoon":
            return dt.hour == 13 or (dt.hour == 14 and dt.minute < 30)
        if name == "closing":
            return dt.hour == 14 and dt.minute >= 30
        return False

    return match


def _choose_fast_profile_cutoff(
    rows: list[dict[str, Any]],
    fast_profile_hold_minutes: int,
    current_cutoff_hour: int,
) -> tuple[int, dict[str, float] | None]:
    fast_rows = [row for row in rows if int(row.get("hold_profile_minutes", 0)) == fast_profile_hold_minutes]
    if not fast_rows:
        return current_cutoff_hour, None
    baseline = _stats_from_rows(fast_rows)
    best_hour = current_cutoff_hour
    best_payload: dict[str, float] | None = None
    for cutoff_hour in (13, 14, 15):
        kept = []
        filtered_out = 0
        for row in fast_rows:
            dt = _parse_kst(str(row.get("detected_at", "")))
            if dt is None:
                kept.append(row)
                continue
            if dt.hour >= cutoff_hour:
                filtered_out += 1
                continue
            kept.append(row)
        if filtered_out == 0 or not kept:
            continue
        stats = _stats_from_rows(kept)
        improvement = stats["total_pnl"] - baseline["total_pnl"]
        if stats["avg_pnl"] <= baseline["avg_pnl"] or improvement <= 0:
            continue
        payload = {
            "kept_count": stats["count"],
            "filtered_count": float(filtered_out),
            "avg_pnl": stats["avg_pnl"],
            "total_pnl": stats["total_pnl"],
            "improvement": improvement,
        }
        if best_payload is None or payload["improvement"] > best_payload["improvement"]:
            best_hour = cutoff_hour
            best_payload = payload
    return best_hour, best_payload


def derive_recommendations(analysis: dict[str, Any]) -> dict[str, Any]:
    runtime_defaults = analysis.get("runtime_defaults", {})
    trade_rows = list(analysis.get("trade_rows", []))
    exit_candidates = analysis.get("condition_scores", {}).get("exit", {}).get("candidates", [])
    best_exit = exit_candidates[0] if exit_candidates else {"params": runtime_defaults}
    recommended_params = {
        "MIN_BUY_CONFIDENCE": int(runtime_defaults.get("min_buy_confidence", 78)),
        "OPENING_MIN_CONFIDENCE": int(runtime_defaults.get("opening_min_confidence", 82)),
        "AFTERNOON_MIN_CONFIDENCE": int(runtime_defaults.get("afternoon_min_confidence", 80)),
        "CLOSING_MIN_CONFIDENCE": int(runtime_defaults.get("closing_min_confidence", 85)),
        "PAPER_TAKE_PROFIT_PCT": float(best_exit["params"].get("paper_take_profit_pct", runtime_defaults.get("paper_take_profit_pct", 2.0))),
        "PAPER_STOP_LOSS_PCT": float(best_exit["params"].get("paper_stop_loss_pct", runtime_defaults.get("paper_stop_loss_pct", -1.5))),
        "TRAILING_STOP_ACTIVATION_PCT": float(best_exit["params"].get("trailing_stop_activation_pct", runtime_defaults.get("trailing_stop_activation_pct", 0.5))),
        "TRAILING_STOP_EARLY_PCT": float(best_exit["params"].get("trailing_stop_early_pct", runtime_defaults.get("trailing_stop_early_pct", 0.5))),
        "TRAILING_STOP_MID_PCT": float(best_exit["params"].get("trailing_stop_mid_pct", runtime_defaults.get("trailing_stop_mid_pct", 0.8))),
        "TRAILING_STOP_LATE_PCT": float(best_exit["params"].get("trailing_stop_late_pct", runtime_defaults.get("trailing_stop_late_pct", 1.0))),
        "MAX_HOLD_MINUTES": int(best_exit["params"].get("max_hold_minutes", runtime_defaults.get("max_hold_minutes", 15))),
        "T5M_LOSS_EXIT_ENABLED": bool(best_exit["params"].get("t5m_loss_exit_enabled", runtime_defaults.get("t5m_loss_exit_enabled", True))),
        "FAST_PROFILE_NO_BUY_AFTER_KST_HOUR": int(runtime_defaults.get("fast_profile_no_buy_after_kst_hour", 14)),
    }
    fast_profile_hold_minutes = int(runtime_defaults.get("fast_profile_hold_minutes", 20))

    rationale: list[str] = []
    evidence: dict[str, Any] = {
        "baseline_total_trades": analysis.get("total_trades", 0),
        "baseline_win_rate": analysis.get("win_rate", 0.0),
        "baseline_total_pnl_pct": analysis.get("total_pnl_pct", 0.0),
    }

    overall_threshold, overall_stats = _choose_threshold(
        trade_rows,
        current=recommended_params["MIN_BUY_CONFIDENCE"],
        minimum=75,
        maximum=90,
    )
    recommended_params["MIN_BUY_CONFIDENCE"] = overall_threshold
    if overall_stats is not None:
        evidence["min_buy_confidence"] = overall_stats
        rationale.append(
            f"Global BUY floor {overall_threshold} selected from executed-trade subset "
            f"(avg={overall_stats['avg_pnl']:+.3f}%, win={overall_stats['win_rate']:.1f}%)."
        )

    for env_key, period_name in (
        ("OPENING_MIN_CONFIDENCE", "opening"),
        ("AFTERNOON_MIN_CONFIDENCE", "afternoon"),
        ("CLOSING_MIN_CONFIDENCE", "closing"),
    ):
        current = recommended_params[env_key]
        period_rows = [row for row in trade_rows if _period_filter(period_name)(row)]
        threshold, stats = _choose_threshold(period_rows, current=current, minimum=75, maximum=90)
        recommended_params[env_key] = threshold
        if stats is not None:
            evidence[period_name] = stats
            rationale.append(
                f"{period_name} floor {threshold} uses {int(stats['count'])} trade(s) "
                f"(avg={stats['avg_pnl']:+.3f}%, win={stats['win_rate']:.1f}%)."
            )
        else:
            rationale.append(f"{period_name} floor kept at {threshold} due to insufficient local sample.")

    cutoff_hour, cutoff_stats = _choose_fast_profile_cutoff(
        trade_rows,
        fast_profile_hold_minutes,
        recommended_params["FAST_PROFILE_NO_BUY_AFTER_KST_HOUR"],
    )
    recommended_params["FAST_PROFILE_NO_BUY_AFTER_KST_HOUR"] = cutoff_hour
    if cutoff_stats is not None:
        evidence["fast_profile_cutoff"] = cutoff_stats
        rationale.append(
            f"Fast-profile cutoff {cutoff_hour}:00 improves kept-trade total PnL by "
            f"{cutoff_stats['improvement']:+.3f}%."
        )

    evidence["best_exit_candidate"] = best_exit
    rationale.append(
        "Exit parameters follow the top-ranked simulation candidate "
        f"(score={best_exit.get('score', 0.0):.3f}, total={best_exit.get('total_pnl', 0.0):+.3f}%)."
    )

    env_block_lines = [f"export {key}={value}" for key, value in recommended_params.items()]
    return {
        "recommended_params": recommended_params,
        "rationale": rationale,
        "evidence": evidence,
        "env_block": "\n".join(env_block_lines),
    }


def render_text(recommendations: dict[str, Any], analysis_path: Path) -> str:
    lines = [
        "Kindshot Strategy Auto-Tune",
        f"analysis: {analysis_path}",
        "",
        "Recommended params:",
    ]
    for key, value in recommendations["recommended_params"].items():
        lines.append(f"  {key}={value}")
    lines.append("")
    lines.append("Rationale:")
    for item in recommendations["rationale"]:
        lines.append(f"  - {item}")
    lines.append("")
    lines.append("Shell exports:")
    lines.extend(f"  {line}" for line in recommendations["env_block"].splitlines())
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", required=True, help="Analysis JSON produced by backtest_analysis.py")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", help="Optional output file path")
    args = parser.parse_args()

    analysis_path = Path(args.analysis)
    analysis = load_analysis(analysis_path)
    recommendations = derive_recommendations(analysis)

    if args.format == "json":
        payload = json.dumps(recommendations, indent=2, ensure_ascii=False)
    else:
        payload = render_text(recommendations, analysis_path)

    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
