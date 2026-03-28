#!/usr/bin/env python3
"""가드레일 시뮬레이션 — 최근 10일 로그에서 v78 기준 vs 이전 기준 비교.

Usage:
    python scripts/guardrail_sim.py [--output reports/guardrail_sim.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kindshot.config import Config
from kindshot.guardrails import (
    DynamicGuardrailProfile,
    GuardrailResult,
    check_guardrails,
)
from kindshot.models import Action
from kindshot.tz import KST


def _parse_decision_time(ev: dict) -> datetime | None:
    """detected_at 또는 disclosed_at에서 KST datetime 추출."""
    for key in ("detected_at", "disclosed_at"):
        raw = ev.get(key)
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)
            return dt.astimezone(KST)
        except (ValueError, TypeError):
            continue
    return None


# v77 이전 기준 (v78에서 완화된 값들의 원래 값)
OLD_THRESHOLDS = {
    "min_buy_confidence": 78,
    "min_intraday_value_vs_adv20d": 0.15,
    "chase_buy_pct": 3.0,
    "fast_profile_no_buy_after_kst_minute": 0,   # 14:00
    "no_buy_after_kst_minute": 0,                 # 15:00
    # orderbook: 100% → 50% (top_ask_notional threshold)
}

OLD_DYNAMIC_PROFILE_OVERRIDES = {
    "min_buy_confidence": 76,     # was 76, now 71
    "afternoon_min_confidence": 78,  # was 78, now 75
}


def _run_guardrail(ev: dict, config: Config, profile: DynamicGuardrailProfile | None = None) -> GuardrailResult:
    """이벤트 로그에서 check_guardrails 파라미터를 재구성하여 호출."""
    ctx = ev.get("ctx") or {}
    decision_time = _parse_decision_time(ev)

    return check_guardrails(
        ticker=ev.get("ticker", ""),
        config=config,
        spread_bps=ctx.get("spread_bps"),
        adv_value_20d=ctx.get("adv_value_20d"),
        ret_today=ctx.get("ret_today"),
        headline=ev.get("headline", ""),
        intraday_value_vs_adv20d=ctx.get("intraday_value_vs_adv20d"),
        delay_ms=ev.get("delay_ms"),
        prior_volume_rate=ctx.get("prior_volume_rate"),
        volume_ratio_vs_avg20d=ctx.get("volume_ratio_vs_avg20d"),
        quote_temp_stop=ctx.get("quote_temp_stop"),
        quote_liquidation_trade=ctx.get("quote_liquidation_trade"),
        top_ask_notional=ctx.get("top_ask_notional"),
        decision_action=Action.BUY if ev.get("decision_action") == "BUY" else Action.SKIP,
        decision_confidence=ev.get("decision_confidence"),
        decision_time_kst=decision_time,
        decision_size_hint=ev.get("decision_size_hint", "M"),
        dynamic_profile=profile,
    )


def _load_events(log_dir: Path, days: int = 10) -> list[dict]:
    """최근 N개 로그 파일에서 POS_STRONG/POS_WEAK 이벤트 로드."""
    log_files = sorted(log_dir.glob("kindshot_*.jsonl"))[-days:]
    events = []
    for f in log_files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "event":
                    continue
                if ev.get("bucket") not in ("POS_STRONG", "POS_WEAK"):
                    continue
                events.append(ev)
    return events


def main() -> None:
    parser = argparse.ArgumentParser(description="가드레일 시뮬레이션")
    parser.add_argument("--output", default="reports/guardrail_sim.json")
    parser.add_argument("--days", type=int, default=10)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    log_dir = project_root / "logs"
    events = _load_events(log_dir, args.days)

    # 가드레일 도달 이벤트만 필터 (quant 통과 + decision 존재)
    guardrail_events = [
        ev for ev in events
        if ev.get("quant_check_passed") is True
        and ev.get("decision_action") is not None
        and ev.get("ctx") is not None
    ]

    # v78 현재 config (기본값)
    config_v78 = Config()

    # v77 이전 config (OLD_THRESHOLDS 적용)
    import os
    old_env = {
        "MIN_BUY_CONFIDENCE": "78",
        "MIN_INTRADAY_VALUE_VS_ADV20D": "0.15",
        "CHASE_BUY_PCT": "3.0",
        "FAST_PROFILE_NO_BUY_AFTER_KST_MINUTE": "0",
        "NO_BUY_AFTER_KST_MINUTE": "0",
    }
    saved = {}
    for k, v in old_env.items():
        saved[k] = os.environ.get(k)
        os.environ[k] = v
    config_old = Config()
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    # Dynamic profiles
    profile_v78 = DynamicGuardrailProfile.from_config(config_v78)
    profile_old = DynamicGuardrailProfile(
        min_buy_confidence=OLD_DYNAMIC_PROFILE_OVERRIDES.get("min_buy_confidence", 76),
        opening_min_confidence=config_old.opening_min_confidence,
        afternoon_min_confidence=OLD_DYNAMIC_PROFILE_OVERRIDES.get("afternoon_min_confidence", 78),
        closing_min_confidence=config_old.closing_min_confidence,
        fast_profile_no_buy_after_kst_hour=config_old.fast_profile_no_buy_after_kst_hour,
        fast_profile_no_buy_after_kst_minute=0,
    )

    # 시뮬레이션 실행
    v78_passed, v78_blocked = 0, 0
    old_passed, old_blocked = 0, 0
    v78_block_reasons: Counter = Counter()
    old_block_reasons: Counter = Counter()
    newly_passed: list[dict] = []  # v78에서 새로 통과된 이벤트
    day_stats: dict[str, dict] = {}

    for ev in guardrail_events:
        result_v78 = _run_guardrail(ev, config_v78, profile_v78)
        result_old = _run_guardrail(ev, config_old, profile_old)

        # 날짜별 집계
        dt = _parse_decision_time(ev)
        day_key = dt.strftime("%Y-%m-%d") if dt else "unknown"
        if day_key not in day_stats:
            day_stats[day_key] = {"v78_passed": 0, "v78_blocked": 0, "old_passed": 0, "old_blocked": 0}

        if result_v78.passed:
            v78_passed += 1
            day_stats[day_key]["v78_passed"] += 1
        else:
            v78_blocked += 1
            v78_block_reasons[result_v78.reason or "UNKNOWN"] += 1
            day_stats[day_key]["v78_blocked"] += 1

        if result_old.passed:
            old_passed += 1
            day_stats[day_key]["old_passed"] += 1
        else:
            old_blocked += 1
            old_block_reasons[result_old.reason or "UNKNOWN"] += 1
            day_stats[day_key]["old_blocked"] += 1

        # 새로 통과된 이벤트
        if result_v78.passed and not result_old.passed:
            newly_passed.append({
                "event_id": ev.get("event_id", "")[:12],
                "ticker": ev.get("ticker"),
                "headline": ev.get("headline", "")[:60],
                "bucket": ev.get("bucket"),
                "confidence": ev.get("decision_confidence"),
                "old_block_reason": result_old.reason,
                "time": day_key,
            })

    total = len(guardrail_events)
    report = {
        "summary": {
            "total_pos_events": len(events),
            "guardrail_eligible_events": total,
            "v78_passed": v78_passed,
            "v78_blocked": v78_blocked,
            "v78_pass_rate_pct": round(v78_passed / total * 100, 1) if total else 0,
            "old_passed": old_passed,
            "old_blocked": old_blocked,
            "old_pass_rate_pct": round(old_passed / total * 100, 1) if total else 0,
            "newly_passed_count": len(newly_passed),
            "pass_rate_improvement_pct": round(
                (v78_passed - old_passed) / total * 100, 1
            ) if total else 0,
        },
        "v78_block_reasons": dict(v78_block_reasons.most_common()),
        "old_block_reasons": dict(old_block_reasons.most_common()),
        "threshold_changes": {
            "min_buy_confidence": {"old": 78, "new": 73},
            "min_intraday_value_vs_adv20d": {"old": 0.15, "new": 0.05},
            "chase_buy_pct": {"old": 3.0, "new": 5.0},
            "fast_profile_no_buy_after": {"old": "14:00", "new": "14:30"},
            "no_buy_after": {"old": "15:00", "new": "15:15"},
            "orderbook_liquidity_pct": {"old": "100%", "new": "50%"},
        },
        "day_stats": dict(sorted(day_stats.items())),
        "newly_passed_events": newly_passed[:30],
    }

    output_path = project_root / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"\nFull report: {output_path}")


if __name__ == "__main__":
    main()
