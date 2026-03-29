#!/usr/bin/env python3
"""전략별 성과 분석 스크립트.

data/trade_history.db에서 트레이드를 로드하여:
1. 전략(버킷/소스) 별 승률·평균수익률·MDD
2. 시간대별 성과 (장 초반/중반/후반)
3. 가드레일 차단 통계
4. exit_type별 분포
를 리포트로 출력한다.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trade_history.db"


# ── helpers ──────────────────────────────────────────────────────────

def _pct(v: float | None) -> str:
    return f"{v:+.2f}%" if v is not None else "N/A"


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def _mdd(returns: list[float]) -> float:
    """누적 수익 기준 최대 낙폭(MDD) 계산."""
    if not returns:
        return 0.0
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for r in returns:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > mdd:
            mdd = dd
    return -mdd  # 음수로 반환


def _hour_slot_label(h: int) -> str:
    """시간대 → 장 구간 라벨."""
    if h <= 9:
        return "초반(08-09)"
    elif h <= 11:
        return "중반(10-11)"
    else:
        return "후반(12+)"


# ── 전략 분류 ─────────────────────────────────────────────────────────

def classify_strategy(row: dict) -> str:
    """트레이드를 전략으로 분류.

    현재 kindshot은 뉴스 기반이 주력이므로 bucket 기준으로 세분화하고,
    향후 TA/Y2I/Alpha 소스가 추가되면 decision_source로 먼저 분기한다.
    """
    src = (row.get("decision_source") or "").strip()
    if src:
        return src.upper()

    bucket = (row.get("bucket") or "").strip()
    if bucket.startswith("POS_STRONG"):
        return "NEWS_STRONG"
    elif bucket.startswith("POS_WEAK"):
        return "NEWS_WEAK_DISABLED"
    elif bucket.startswith("NEG"):
        return "NEWS_NEG"
    elif bucket == "UNKNOWN":
        return "NEWS_UNKNOWN"
    return "NEWS_OTHER"


# ── 데이터 로드 ───────────────────────────────────────────────────────

def load_trades(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM trades ORDER BY date, detected_at")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ── 리포트 섹션 ───────────────────────────────────────────────────────

def section_overall(trades: list[dict]) -> str:
    lines = ["=" * 60, "  KINDSHOT 전략 성과 분석 리포트", "=" * 60, ""]
    n = len(trades)
    rets = [t["exit_ret_pct"] for t in trades if t["exit_ret_pct"] is not None]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    avg_ret = _safe_div(sum(rets), len(rets)) if rets else 0
    win_rate = _safe_div(len(wins), len(rets)) * 100

    dates = sorted(set(t["date"] for t in trades))
    versions = sorted(set(t["version_tag"] or "unknown" for t in trades))

    lines.append(f"기간: {dates[0] if dates else 'N/A'} ~ {dates[-1] if dates else 'N/A'}")
    lines.append(f"버전: {', '.join(versions)}")
    lines.append(f"총 트레이드: {n}건 (수익률 있는 건: {len(rets)}건)")
    lines.append(f"승률: {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)")
    lines.append(f"평균 수익률: {_pct(avg_ret)}")
    lines.append(f"총 수익률: {_pct(sum(rets))}")
    lines.append(f"MDD: {_pct(_mdd(rets))}")
    lines.append(f"최대 수익: {_pct(max(rets) if rets else None)}")
    lines.append(f"최대 손실: {_pct(min(rets) if rets else None)}")
    lines.append("")
    return "\n".join(lines)


def section_by_strategy(trades: list[dict]) -> str:
    lines = ["-" * 60, "  1. 전략별 성과", "-" * 60, ""]

    groups: dict[str, list[dict]] = {}
    for t in trades:
        s = classify_strategy(t)
        groups.setdefault(s, []).append(t)

    for strat in sorted(groups.keys()):
        tlist = groups[strat]
        rets = [t["exit_ret_pct"] for t in tlist if t["exit_ret_pct"] is not None]
        wins = [r for r in rets if r > 0]
        avg = _safe_div(sum(rets), len(rets)) if rets else 0
        wr = _safe_div(len(wins), len(rets)) * 100
        lines.append(f"▸ {strat} ({len(tlist)}건)")
        lines.append(f"  승률: {wr:.1f}%  |  평균: {_pct(avg)}  |  합계: {_pct(sum(rets))}  |  MDD: {_pct(_mdd(rets))}")
        lines.append("")

    return "\n".join(lines)


def section_by_version(trades: list[dict]) -> str:
    lines = ["-" * 60, "  2. 버전별 성과", "-" * 60, ""]

    groups: dict[str, list[dict]] = {}
    for t in trades:
        v = t["version_tag"] or "unknown"
        groups.setdefault(v, []).append(t)

    for ver in sorted(groups.keys()):
        tlist = groups[ver]
        rets = [t["exit_ret_pct"] for t in tlist if t["exit_ret_pct"] is not None]
        wins = [r for r in rets if r > 0]
        avg = _safe_div(sum(rets), len(rets)) if rets else 0
        wr = _safe_div(len(wins), len(rets)) * 100
        lines.append(f"▸ {ver} ({len(tlist)}건)")
        lines.append(f"  승률: {wr:.1f}%  |  평균: {_pct(avg)}  |  합계: {_pct(sum(rets))}  |  MDD: {_pct(_mdd(rets))}")
        lines.append("")

    return "\n".join(lines)


def section_by_time(trades: list[dict]) -> str:
    lines = ["-" * 60, "  3. 시간대별 성과", "-" * 60, ""]

    groups: dict[str, list[dict]] = {}
    for t in trades:
        label = _hour_slot_label(t.get("hour_slot", 0))
        groups.setdefault(label, []).append(t)

    for slot in sorted(groups.keys()):
        tlist = groups[slot]
        rets = [t["exit_ret_pct"] for t in tlist if t["exit_ret_pct"] is not None]
        wins = [r for r in rets if r > 0]
        avg = _safe_div(sum(rets), len(rets)) if rets else 0
        wr = _safe_div(len(wins), len(rets)) * 100
        lines.append(f"▸ {slot} ({len(tlist)}건)")
        lines.append(f"  승률: {wr:.1f}%  |  평균: {_pct(avg)}  |  합계: {_pct(sum(rets))}")
        lines.append("")

    return "\n".join(lines)


def section_exit_type(trades: list[dict]) -> str:
    lines = ["-" * 60, "  4. 청산 유형별 성과", "-" * 60, ""]

    groups: dict[str, list[dict]] = {}
    for t in trades:
        et = t.get("exit_type") or "unknown"
        groups.setdefault(et, []).append(t)

    for et in sorted(groups.keys()):
        tlist = groups[et]
        rets = [t["exit_ret_pct"] for t in tlist if t["exit_ret_pct"] is not None]
        wins = [r for r in rets if r > 0]
        avg = _safe_div(sum(rets), len(rets)) if rets else 0
        wr = _safe_div(len(wins), len(rets)) * 100
        lines.append(f"▸ {et} ({len(tlist)}건)")
        lines.append(f"  승률: {wr:.1f}%  |  평균: {_pct(avg)}  |  합계: {_pct(sum(rets))}")
        lines.append("")

    return "\n".join(lines)


def section_guardrail(trades: list[dict]) -> str:
    lines = ["-" * 60, "  5. 가드레일 차단 통계", "-" * 60, ""]

    blocked = [t for t in trades if t.get("guardrail_result")]
    skipped = [t for t in trades if t.get("skip_stage")]
    passed = [t for t in trades if not t.get("guardrail_result") and not t.get("skip_stage")]

    lines.append(f"통과: {len(passed)}건  |  가드레일 차단: {len(blocked)}건  |  스킵: {len(skipped)}건")
    lines.append("")

    if blocked:
        reasons: dict[str, int] = {}
        for t in blocked:
            r = t["guardrail_result"]
            reasons[r] = reasons.get(r, 0) + 1
        lines.append("차단 사유:")
        for reason, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
            lines.append(f"  {reason}: {cnt}건")
        lines.append("")

    if skipped:
        stages: dict[str, int] = {}
        for t in skipped:
            s = t["skip_stage"]
            stages[s] = stages.get(s, 0) + 1
        lines.append("스킵 단계:")
        for stage, cnt in sorted(stages.items(), key=lambda x: -x[1]):
            lines.append(f"  {stage}: {cnt}건")
        lines.append("")

    return "\n".join(lines)


def section_top_bottom(trades: list[dict]) -> str:
    lines = ["-" * 60, "  6. 개별 트레이드 상세", "-" * 60, ""]

    sorted_trades = sorted(
        [t for t in trades if t["exit_ret_pct"] is not None],
        key=lambda t: t["exit_ret_pct"],
        reverse=True,
    )

    lines.append(f"{'날짜':<10} {'종목':<8} {'전략':<14} {'수익률':>8} {'피크':>8} {'청산':>16} {'헤드라인':<30}")
    lines.append("─" * 100)
    for t in sorted_trades:
        date = t["date"]
        corp = (t["corp_name"] or t["ticker"])[:8]
        strat = classify_strategy(t)[:14]
        ret = _pct(t["exit_ret_pct"])
        peak = _pct(t.get("peak_ret_pct"))
        exit_info = f"{t.get('exit_type', '')} {t.get('exit_horizon', '')}"
        headline = (t.get("headline") or "")[:30]
        lines.append(f"{date:<10} {corp:<8} {strat:<14} {ret:>8} {peak:>8} {exit_info:>16} {headline}")

    lines.append("")
    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Kindshot 전략별 성과 분석")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="trade_history.db 경로")
    parser.add_argument("--save", type=Path, default=None, help="리포트 저장 경로")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"ERROR: DB not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    trades = load_trades(args.db)
    if not trades:
        print("트레이드 데이터 없음.", file=sys.stderr)
        sys.exit(1)

    report = "\n".join([
        section_overall(trades),
        section_by_strategy(trades),
        section_by_version(trades),
        section_by_time(trades),
        section_exit_type(trades),
        section_guardrail(trades),
        section_top_bottom(trades),
    ])

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    report += f"\n생성: {timestamp}\n"

    print(report)

    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(report, encoding="utf-8")
        print(f"\n리포트 저장: {args.save}")


if __name__ == "__main__":
    main()
