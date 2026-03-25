#!/usr/bin/env python3
"""Strategy comparison report — 전략 세그먼트별 상세 비교 분석.

로그 파일(logs/kindshot_*.jsonl)에서 event/decision/price_snapshot을 읽어
다차원 분석 리포트를 생성한다.

분석 축:
  - 버킷 타입: POS_STRONG vs POS_WEAK
  - Confidence 구간: 65-69, 70-79, 80-89, 90+
  - 시간대: morning(09-10), midday(10-14), afternoon(14-15:30)
  - Exit 타입: TP, SL, TRAIL, MAX_HOLD
  - 헤드라인 키워드 카테고리
  - Skip 분석: 사유별 카운트, 놓친 기회

사용법:
    python scripts/strategy_comparison.py
    python scripts/strategy_comparison.py --date 20260318
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional


# ── 상수 ──

TP_PCT = 1.5
SL_PCT = -1.0
TRAIL_ACTIVATION = 0.8
TRAIL_DROP = 0.8
MAX_HOLD_HORIZON = "t+30m"
HORIZONS = ["t+30s", "t+1m", "t+2m", "t+5m", "t+30m", "close"]

KEYWORD_CATEGORIES: dict[str, list[str]] = {
    "supply_contract": ["공급계약", "수주", "납품", "계약 체결", "공급 계약"],
    "patent": ["특허", "지식재산", "IP"],
    "ma": ["인수", "합병", "M&A", "합작", "지분 취득", "피인수"],
    "earnings": ["실적", "영업이익", "매출", "순이익", "흑자", "실적 개선"],
    "buyback": ["자사주 소각", "자사주 매입", "자기주식"],
}


# ── 데이터 로딩 (replay_sim.py 동일 패턴) ──

def load_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def find_log_files(log_dir: Path, date_filter: str = "") -> list[Path]:
    pattern = f"kindshot_{date_filter}*.jsonl" if date_filter else "kindshot_*.jsonl"
    return sorted(log_dir.glob(pattern))


# ── TP/SL/Trailing 판정 (replay_sim.py 동일 로직) ──

def compute_exit(rets: dict[str, float]) -> tuple[Optional[str], Optional[str], Optional[float]]:
    """exit_type, exit_horizon, exit_ret 반환."""
    exit_type: Optional[str] = None
    exit_horizon: Optional[str] = None
    exit_ret: Optional[float] = None
    peak = 0.0

    for h in HORIZONS:
        r = rets.get(h)
        if r is None:
            continue
        peak = max(peak, r)
        if exit_type is not None:
            continue
        if r >= TP_PCT:
            exit_type, exit_horizon, exit_ret = "TP", h, TP_PCT
        elif r <= SL_PCT:
            exit_type, exit_horizon, exit_ret = "SL", h, SL_PCT
        elif peak >= TRAIL_ACTIVATION and r <= peak - TRAIL_DROP:
            exit_type, exit_horizon, exit_ret = "TRAIL", h, r
        elif h == MAX_HOLD_HORIZON:
            exit_type, exit_horizon, exit_ret = "MAX_HOLD", h, r

    if exit_ret is None:
        close = rets.get("close")
        if close is not None:
            exit_ret = close

    return exit_type, exit_horizon, exit_ret


# ── 시간대 분류 ──

def classify_time_of_day(detected_at_str: Optional[str]) -> Optional[str]:
    """KST detected_at 문자열에서 시간대 분류."""
    if not detected_at_str:
        return None
    try:
        # "2026-03-12T09:30:00.123456+09:00" 형식
        time_part = detected_at_str[11:16]  # "HH:MM"
        h, m = int(time_part[:2]), int(time_part[3:5])
        minutes = h * 60 + m
        if 9 * 60 <= minutes < 10 * 60:
            return "morning"
        elif 10 * 60 <= minutes < 14 * 60:
            return "midday"
        elif 14 * 60 <= minutes < 15 * 60 + 30:
            return "afternoon"
        return None
    except Exception:
        return None


# ── 키워드 카테고리 분류 ──

def classify_keyword_category(keyword_hits: list[str]) -> list[str]:
    cats = []
    for cat, kws in KEYWORD_CATEGORIES.items():
        for kw in kws:
            if any(kw in hit for hit in keyword_hits):
                cats.append(cat)
                break
    return cats if cats else ["other"]


# ── 통계 헬퍼 ──

def stats(rets: list[float]) -> dict:
    if not rets:
        return {"count": 0, "win_rate": None, "avg": None, "pf": None, "max": None, "min": None}
    wins = [r for r in rets if r > 0]
    gross_win = sum(r for r in rets if r > 0)
    gross_loss = abs(sum(r for r in rets if r < 0))
    pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    return {
        "count": len(rets),
        "wins": len(wins),
        "win_rate": len(wins) / len(rets) * 100,
        "avg": sum(rets) / len(rets),
        "pf": pf,
        "max": max(rets),
        "min": min(rets),
    }


# ── 메인 로딩 및 집계 ──

def load_all_data(log_dir: Path, date_filter: str = "") -> dict:
    log_files = find_log_files(log_dir, date_filter)
    if not log_files:
        print(f"로그 파일 없음: {log_dir}/kindshot_{date_filter}*.jsonl", file=sys.stderr)
        return {}

    all_events: dict[str, dict] = {}
    all_decisions: dict[str, dict] = {}
    all_snapshots: dict[str, dict[str, dict]] = defaultdict(dict)
    bucket_counts: dict[str, int] = defaultdict(int)
    skip_counts: dict[str, int] = defaultdict(int)

    for lf in log_files:
        for rec in load_jsonl(lf):
            eid = rec.get("event_id", "")
            rtype = rec.get("type")
            if rtype == "event":
                if rec.get("skip_reason") == "DUPLICATE":
                    continue
                all_events[eid] = rec
                bucket_counts[rec.get("bucket", "?")] += 1
                if rec.get("skip_reason"):
                    skip_counts[rec["skip_reason"]] += 1
            elif rtype == "decision":
                all_decisions[eid] = rec
            elif rtype == "price_snapshot":
                h = rec.get("horizon", "")
                if eid and h:
                    all_snapshots[eid][h] = rec

    # snapshot 디렉터리도 로드 (replay_sim.py 동일)
    snapshot_dir = log_dir.parent / "data" / "runtime" / "price_snapshots"
    if snapshot_dir.exists():
        snap_files = sorted(snapshot_dir.glob(
            f"{date_filter}*.jsonl" if date_filter else "*.jsonl"
        ))
        for sf in snap_files:
            for rec in load_jsonl(sf):
                eid = rec.get("event_id", "")
                h = rec.get("horizon", "")
                if eid and h:
                    all_snapshots[eid][h] = rec

    buy_decisions = {eid: d for eid, d in all_decisions.items() if d.get("action") == "BUY"}
    skip_decisions = {eid: d for eid, d in all_decisions.items() if d.get("action") == "SKIP"}

    # BUY 결과 구성
    buy_results: list[dict] = []
    for eid, dec in buy_decisions.items():
        ev = all_events.get(eid, {})
        snaps = all_snapshots.get(eid, {})

        rets: dict[str, float] = {}
        for h in HORIZONS:
            snap = snaps.get(h, {})
            r = snap.get("ret_long_vs_t0")
            if r is not None:
                rets[h] = r * 100

        exit_type, exit_horizon, exit_ret = compute_exit(rets)

        keyword_hits = ev.get("keyword_hits") or []
        detected_at = ev.get("detected_at", "")

        buy_results.append({
            "event_id": eid,
            "ticker": ev.get("ticker", "?"),
            "headline": ev.get("headline", ""),
            "bucket": ev.get("bucket", "?"),
            "keyword_hits": keyword_hits,
            "keyword_cats": classify_keyword_category(keyword_hits),
            "confidence": dec.get("confidence", 0),
            "size_hint": dec.get("size_hint", "?"),
            "detected_at": detected_at,
            "time_of_day": classify_time_of_day(detected_at),
            "rets": rets,
            "close_ret": rets.get("close"),
            "exit_type": exit_type,
            "exit_horizon": exit_horizon,
            "exit_ret": exit_ret,
            "llm_skip_reason": dec.get("reason", ""),
        })

    # 놓친 기회: POS 시그널인데 QUANT/GUARDRAIL에서 필터된 이벤트
    missed_pos: list[dict] = []
    for eid, ev in all_events.items():
        bucket = ev.get("bucket", "")
        skip_stage = ev.get("skip_stage", "")
        skip_reason = ev.get("skip_reason", "")
        if bucket in ("POS_STRONG", "POS_WEAK") and skip_stage in ("QUANT", "GUARDRAIL"):
            missed_pos.append({
                "event_id": eid,
                "ticker": ev.get("ticker", "?"),
                "headline": ev.get("headline", "")[:60],
                "bucket": bucket,
                "skip_stage": skip_stage,
                "skip_reason": skip_reason,
                "keyword_hits": ev.get("keyword_hits") or [],
            })

    return {
        "log_files": [str(f) for f in log_files],
        "total_events": len(all_events),
        "bucket_counts": dict(bucket_counts),
        "skip_counts": dict(skip_counts),
        "total_decisions": len(all_decisions),
        "buy_count": len(buy_decisions),
        "skip_count": len(skip_decisions),
        "buy_results": buy_results,
        "missed_pos": missed_pos,
    }


# ── 분석 ──

def analyse(data: dict) -> dict:
    buys = data["buy_results"]

    # ── By bucket type ──
    bucket_rets: dict[str, list[float]] = defaultdict(list)
    for b in buys:
        er = b.get("exit_ret")
        if er is not None:
            bucket_rets[b["bucket"]].append(er)

    # ── By confidence band ──
    conf_rets: dict[str, list[float]] = defaultdict(list)
    for b in buys:
        er = b.get("exit_ret")
        if er is None:
            continue
        c = b["confidence"]
        if c >= 90:
            conf_rets["90+"].append(er)
        elif c >= 80:
            conf_rets["80-89"].append(er)
        elif c >= 70:
            conf_rets["70-79"].append(er)
        elif c >= 65:
            conf_rets["65-69"].append(er)
        else:
            conf_rets["<65"].append(er)

    # ── By time-of-day ──
    tod_rets: dict[str, list[float]] = defaultdict(list)
    for b in buys:
        er = b.get("exit_ret")
        tod = b.get("time_of_day")
        if er is not None and tod:
            tod_rets[tod].append(er)

    # ── By exit type ──
    exit_rets: dict[str, list[float]] = defaultdict(list)
    for b in buys:
        er = b.get("exit_ret")
        et = b.get("exit_type") or "NONE"
        if er is not None:
            exit_rets[et].append(er)

    # exit type count (including those without exit_ret)
    exit_counts: dict[str, int] = defaultdict(int)
    for b in buys:
        et = b.get("exit_type") or "NONE"
        exit_counts[et] += 1

    # ── By keyword category ──
    kw_rets: dict[str, list[float]] = defaultdict(list)
    for b in buys:
        er = b.get("exit_ret")
        if er is None:
            continue
        for cat in b.get("keyword_cats", ["other"]):
            kw_rets[cat].append(er)

    # ── Per-horizon stats (all BUY trades) ──
    horizon_rets: dict[str, list[float]] = defaultdict(list)
    for b in buys:
        for h in HORIZONS:
            r = b["rets"].get(h)
            if r is not None:
                horizon_rets[h].append(r)

    # ── Skip analysis ──
    missed_by_reason: dict[str, int] = defaultdict(int)
    for m in data["missed_pos"]:
        missed_by_reason[m["skip_reason"]] += 1

    return {
        "by_bucket": {k: stats(v) for k, v in bucket_rets.items()},
        "by_confidence": {
            k: stats(conf_rets[k])
            for k in ["65-69", "70-79", "80-89", "90+", "<65"]
            if k in conf_rets
        },
        "by_time_of_day": {k: stats(v) for k, v in tod_rets.items()},
        "by_exit_type": {
            k: {"count": exit_counts[k], **stats(exit_rets.get(k, []))}
            for k in sorted(exit_counts)
        },
        "by_keyword_category": {k: stats(v) for k, v in kw_rets.items()},
        "by_horizon": {k: stats(v) for k, v in horizon_rets.items()},
        "skip_analysis": {
            "top_skip_reasons": dict(
                sorted(data["skip_counts"].items(), key=lambda x: -x[1])
            ),
            "missed_pos_by_reason": dict(
                sorted(missed_by_reason.items(), key=lambda x: -x[1])
            ),
            "missed_pos_total": len(data["missed_pos"]),
        },
        "overall": stats([b["exit_ret"] for b in buys if b.get("exit_ret") is not None]),
        "meta": {
            "log_files": data["log_files"],
            "total_events": data["total_events"],
            "bucket_counts": data["bucket_counts"],
            "total_decisions": data["total_decisions"],
            "buy_count": data["buy_count"],
            "skip_count": data["skip_count"],
        },
    }


# ── 출력 ──

def fmt_pct(v: Optional[float]) -> str:
    return f"{v:+.2f}%" if v is not None else "N/A"


def fmt_rate(v: Optional[float]) -> str:
    return f"{v:.1f}%" if v is not None else "N/A"


def fmt_pf(v: Optional[float]) -> str:
    if v is None:
        return "N/A"
    if v == float("inf"):
        return "inf"
    return f"{v:.2f}"


def print_stats_row(label: str, s: dict, width: int = 18) -> None:
    if s["count"] == 0:
        print(f"  {label:<{width}}  (no data)")
        return
    print(
        f"  {label:<{width}}"
        f"  n={s['count']:>3}"
        f"  win={fmt_rate(s.get('win_rate')):>6}"
        f"  avg={fmt_pct(s.get('avg')):>8}"
        f"  pf={fmt_pf(s.get('pf')):>6}"
        f"  max={fmt_pct(s.get('max')):>8}"
        f"  min={fmt_pct(s.get('min')):>8}"
    )


def print_report(data: dict, analysis: dict) -> None:
    meta = analysis["meta"]
    overall = analysis["overall"]

    print("=" * 80)
    print("  STRATEGY COMPARISON REPORT")
    print("=" * 80)
    print(f"  로그 파일: {len(meta['log_files'])}개")
    for lf in meta["log_files"]:
        print(f"    {Path(lf).name}")
    print(f"  총 이벤트: {meta['total_events']}건")
    print(f"  LLM 판단: {meta['total_decisions']}건  BUY={meta['buy_count']}  SKIP={meta['skip_count']}")
    print()

    # 버킷 분포
    print("  [이벤트 버킷 분포]")
    for b in ["POS_STRONG", "POS_WEAK", "NEG_STRONG", "NEG_WEAK", "IGNORE", "UNKNOWN"]:
        cnt = meta["bucket_counts"].get(b, 0)
        if cnt:
            print(f"    {b}: {cnt}")
    print()

    # 전체 성과
    print(f"  [전체 BUY 성과] ({overall['count']}건)")
    print_stats_row("ALL", overall)
    print()

    # By bucket
    print("  [버킷 타입별]")
    print(f"  {'bucket':<18}  {'n':>4}  {'win%':>6}  {'avg':>8}  {'pf':>6}  {'max':>8}  {'min':>8}")
    print(f"  {'-'*18}  {'-'*4}  {'-'*6}  {'-'*8}  {'-'*6}  {'-'*8}  {'-'*8}")
    for bucket in ["POS_STRONG", "POS_WEAK"]:
        s = analysis["by_bucket"].get(bucket)
        if s:
            print_stats_row(bucket, s)
    print()

    # By confidence
    print("  [Confidence 구간별]")
    print(f"  {'band':<18}  {'n':>4}  {'win%':>6}  {'avg':>8}  {'pf':>6}  {'max':>8}  {'min':>8}")
    print(f"  {'-'*18}  {'-'*4}  {'-'*6}  {'-'*8}  {'-'*6}  {'-'*8}  {'-'*8}")
    for band in ["90+", "80-89", "70-79", "65-69", "<65"]:
        s = analysis["by_confidence"].get(band)
        if s:
            print_stats_row(band, s)
    print()

    # By time-of-day
    print("  [시간대별]")
    print(f"  {'time':<18}  {'n':>4}  {'win%':>6}  {'avg':>8}  {'pf':>6}  {'max':>8}  {'min':>8}")
    print(f"  {'-'*18}  {'-'*4}  {'-'*6}  {'-'*8}  {'-'*6}  {'-'*8}  {'-'*8}")
    for tod in ["morning", "midday", "afternoon"]:
        s = analysis["by_time_of_day"].get(tod)
        if s:
            print_stats_row(tod, s)
    print()

    # By exit type
    print("  [Exit 타입별]")
    print(f"  {'exit_type':<18}  {'cnt':>4}  {'win%':>6}  {'avg':>8}  {'pf':>6}  {'max':>8}  {'min':>8}")
    print(f"  {'-'*18}  {'-'*4}  {'-'*6}  {'-'*8}  {'-'*6}  {'-'*8}  {'-'*8}")
    for et in ["TP", "SL", "TRAIL", "MAX_HOLD", "NONE"]:
        s = analysis["by_exit_type"].get(et)
        if s:
            cnt = s.get("count", 0)
            label = f"{et} ({cnt}건)"
            print_stats_row(label, s, width=22)
    print()

    # By keyword category
    print("  [헤드라인 키워드 카테고리별]")
    print(f"  {'category':<18}  {'n':>4}  {'win%':>6}  {'avg':>8}  {'pf':>6}  {'max':>8}  {'min':>8}")
    print(f"  {'-'*18}  {'-'*4}  {'-'*6}  {'-'*8}  {'-'*6}  {'-'*8}  {'-'*8}")
    cat_order = ["supply_contract", "patent", "ma", "earnings", "buyback", "other"]
    for cat in cat_order:
        s = analysis["by_keyword_category"].get(cat)
        if s:
            print_stats_row(cat, s)
    # any extra categories not in the preset order
    for cat, s in sorted(analysis["by_keyword_category"].items()):
        if cat not in cat_order:
            print_stats_row(cat, s)
    print()

    # By horizon (raw, not exit)
    print("  [시간 지평별 평균 수익 (BUY 전체, exit 미적용)]")
    print(f"  {'horizon':<12}  {'n':>4}  {'avg':>8}  {'win%':>6}")
    print(f"  {'-'*12}  {'-'*4}  {'-'*8}  {'-'*6}")
    for h in HORIZONS:
        s = analysis["by_horizon"].get(h)
        if s and s["count"] > 0:
            print(f"  {h:<12}  {s['count']:>4}  {fmt_pct(s['avg']):>8}  {fmt_rate(s.get('win_rate')):>6}")
    print()

    # Skip analysis
    skip = analysis["skip_analysis"]
    print("  [Skip 사유 상위]")
    for reason, cnt in list(skip["top_skip_reasons"].items())[:15]:
        print(f"    {reason}: {cnt}")
    print()

    print(f"  [놓친 기회 — POS 시그널 QUANT/GUARDRAIL 필터] (총 {skip['missed_pos_total']}건)")
    for reason, cnt in list(skip["missed_pos_by_reason"].items())[:10]:
        print(f"    {reason}: {cnt}")
    print()

    print("=" * 80)


# ── JSON 저장 ──

def save_json(analysis: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2, default=str)
    print(f"JSON saved: {out_path}")


# ── CLI ──

def main() -> None:
    date_filter = ""
    for i, arg in enumerate(sys.argv):
        if arg == "--date" and i + 1 < len(sys.argv):
            date_filter = sys.argv[i + 1]

    project_root = Path(__file__).resolve().parent.parent
    log_dir = project_root / "logs"
    out_path = project_root / ".omc" / "sim_comparison.json"

    data = load_all_data(log_dir, date_filter)
    if not data:
        sys.exit(1)

    analysis = analyse(data)

    print_report(data, analysis)
    save_json(analysis, out_path)


if __name__ == "__main__":
    main()
