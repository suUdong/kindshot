#!/usr/bin/env python3
"""Shadow snapshot 기회비용 분석.

차단된 BUY 시그널(shadow_ prefix)의 가격 추적 데이터를 분석하여
guardrail이 실제로 수익 기회를 놓치고 있는지 판단한다.

Usage:
    python scripts/shadow_analysis.py [--snapshot-dir DATA_DIR] [--dates 20260327 20260328]
    python scripts/shadow_analysis.py --log-dir logs [--dates 20260327]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kindshot.news_category import classify_news_type
from kindshot.tz import KST as _KST

# 기본 TP/SL (config.py 기본값)
DEFAULT_TP_PCT = 2.0
DEFAULT_SL_PCT = -1.5


@dataclass
class ShadowTrade:
    """차단된 BUY의 가상 트레이드."""
    event_id: str
    date: str
    ticker: str
    headline: str
    bucket: str
    confidence: int
    skip_reason: str
    news_type: str = "other"  # v67: 뉴스 카테고리 (contract, mna, etc.)
    t0_price: float = 0.0
    snapshots: dict[str, float] = field(default_factory=dict)  # horizon -> price
    returns: dict[str, float] = field(default_factory=dict)    # horizon -> return %
    max_gain_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    virtual_exit_type: str = "NO_DATA"  # TP, SL, HOLD
    virtual_exit_pnl: float = 0.0
    virtual_exit_horizon: str = ""
    detected_hour_kst: int = -1
    price_sources: tuple[str, ...] = field(default_factory=tuple)
    flat_price: bool = False


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


def load_shadow_data(
    snapshot_dir: Path | None = None,
    log_dir: Path | None = None,
    dates: list[str] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """shadow snapshot과 관련 이벤트/결정 데이터 로드."""
    all_events: list[dict] = []
    all_decisions: list[dict] = []
    all_snapshots: list[dict] = []

    # 1) snapshot 디렉토리에서 shadow_ prefix 스냅샷 로드
    if snapshot_dir and snapshot_dir.exists():
        for f in sorted(snapshot_dir.glob("*.jsonl")):
            date_str = f.stem
            if dates and date_str not in dates:
                continue
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "price_snapshot":
                    all_snapshots.append(rec)

    # 2) log 디렉토리에서 이벤트/결정 로드
    if log_dir and log_dir.exists():
        for f in sorted(log_dir.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = rec.get("type", rec.get("record_type", ""))
                if t == "event":
                    all_events.append(rec)
                elif t == "decision":
                    all_decisions.append(rec)
                elif t == "price_snapshot":
                    # log에도 snapshot이 있을 수 있음
                    if rec not in all_snapshots:
                        all_snapshots.append(rec)

    return all_events, all_decisions, all_snapshots


def build_shadow_trades(
    events: list[dict],
    snapshots: list[dict],
    tp_pct: float = DEFAULT_TP_PCT,
    sl_pct: float = DEFAULT_SL_PCT,
) -> list[ShadowTrade]:
    """shadow_ prefix 스냅샷으로 가상 트레이드 구성."""

    # shadow_ prefix 스냅샷만 필터링, event_id별 그룹핑
    shadow_snaps: dict[str, dict[str, float]] = defaultdict(dict)
    shadow_dates: dict[str, str] = {}
    shadow_sources: dict[str, set[str]] = defaultdict(set)
    for s in snapshots:
        eid = s.get("event_id", "")
        if not eid.startswith("shadow_"):
            continue
        horizon = s.get("horizon", "")
        px = s.get("px")
        if horizon and px is not None:
            shadow_snaps[eid][horizon] = float(px)
        ts = s.get("ts", "")
        if ts:
            shadow_dates[eid] = ts[:10]
        price_source = s.get("price_source")
        if price_source:
            shadow_sources[eid].add(str(price_source))

    if not shadow_snaps:
        return []

    # 원본 event_id로 이벤트 정보 매칭 (shadow_evt123 -> evt123)
    event_map: dict[str, dict] = {}
    for ev in events:
        event_map[ev.get("event_id", "")] = ev

    trades: list[ShadowTrade] = []
    horizons = ["t+30s", "t+1m", "t+2m", "t+5m", "t+10m", "t+15m", "t+20m", "t+30m", "close"]

    for shadow_eid, snaps in shadow_snaps.items():
        t0 = snaps.get("t0", 0.0)
        if t0 <= 0:
            continue

        # 원본 event_id로 이벤트 정보 조회
        original_eid = shadow_eid.replace("shadow_", "", 1)
        ev = event_map.get(original_eid, {})
        detected_at = str(ev.get("detected_at", ""))
        detected_hour_kst = _parse_kst_hour(detected_at)

        # horizon별 수익률 계산
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
        exit_horizon = ""
        for h in horizons:
            if h not in returns:
                continue
            ret = returns[h]
            if ret >= tp_pct:
                exit_type = "TP"
                exit_pnl = ret
                exit_horizon = h
                break
            elif ret <= sl_pct:
                exit_type = "SL"
                exit_pnl = ret
                exit_horizon = h
                break
            exit_pnl = ret
            exit_horizon = h

        # HOLD인 경우 마지막 가용 horizon의 수익률 사용
        if exit_type == "HOLD" and returns:
            last_h = list(returns.keys())[-1]
            exit_pnl = returns[last_h]
            exit_horizon = last_h

        observed_prices = [float(px) for px in snaps.values() if px is not None and px > 0]
        flat_price = len(observed_prices) >= 2 and len({round(px, 6) for px in observed_prices}) == 1

        full_headline = ev.get("headline", "")
        news_type = classify_news_type(full_headline)

        trade = ShadowTrade(
            event_id=shadow_eid,
            date=shadow_dates.get(shadow_eid, ""),
            ticker=ev.get("ticker", "?"),
            headline=full_headline[:60],
            bucket=ev.get("bucket", "?"),
            confidence=int(ev.get("decision_confidence", 0)),
            skip_reason=ev.get("skip_reason", ""),
            news_type=news_type,
            t0_price=t0,
            snapshots=snaps,
            returns=returns,
            max_gain_pct=max_gain,
            max_drawdown_pct=max_dd,
            virtual_exit_type=exit_type,
            virtual_exit_pnl=exit_pnl,
            virtual_exit_horizon=exit_horizon,
            detected_hour_kst=detected_hour_kst,
            price_sources=tuple(sorted(shadow_sources.get(shadow_eid, set()))),
            flat_price=flat_price,
        )
        trades.append(trade)

    return trades


def render_report(trades: list[ShadowTrade], tp_pct: float, sl_pct: float) -> str:
    """기회비용 분석 리포트 생성."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("  Shadow Snapshot 기회비용 분석 리포트")
    lines.append(f"  가상 TP: {tp_pct:+.1f}%  |  가상 SL: {sl_pct:+.1f}%")
    lines.append("=" * 70)

    if not trades:
        lines.append("\n  No shadow data — 차단된 BUY 시그널이 아직 없습니다.")
        lines.append("  v66 배포 후 guardrail 차단이 발생하면 데이터가 수집됩니다.")
        return "\n".join(lines)

    # 1. 종합 통계
    total = len(trades)
    tp_trades = [t for t in trades if t.virtual_exit_type == "TP"]
    sl_trades = [t for t in trades if t.virtual_exit_type == "SL"]
    hold_trades = [t for t in trades if t.virtual_exit_type == "HOLD"]

    virtual_win_rate = len(tp_trades) / total * 100 if total else 0
    total_pnl = sum(t.virtual_exit_pnl for t in trades)
    avg_pnl = total_pnl / total if total else 0
    avg_max_gain = sum(t.max_gain_pct for t in trades) / total if total else 0
    avg_max_dd = sum(t.max_drawdown_pct for t in trades) / total if total else 0

    lines.append(f"\n{'─' * 70}")
    lines.append("  1. 종합 통계")
    lines.append(f"{'─' * 70}")
    lines.append(f"  총 차단된 BUY:       {total}")
    lines.append(f"  가상 TP 도달:        {len(tp_trades)} ({virtual_win_rate:.1f}%)")
    lines.append(f"  가상 SL 도달:        {len(sl_trades)}")
    lines.append(f"  미도달 (HOLD):       {len(hold_trades)}")
    lines.append(f"  가상 총 P&L:         {total_pnl:+.2f}%")
    lines.append(f"  가상 평균 P&L:       {avg_pnl:+.2f}%")
    lines.append(f"  평균 최대 수익:      {avg_max_gain:+.2f}%")
    lines.append(f"  평균 최대 낙폭:      {avg_max_dd:+.2f}%")

    # 2. 놓친 수익 기회 (가상 TP 달성한 트레이드)
    if tp_trades:
        lines.append(f"\n{'─' * 70}")
        lines.append("  2. 놓친 수익 기회 (가상 TP 달성)")
        lines.append(f"{'─' * 70}")
        lines.append(f"  {'날짜':<12} {'종목':<10} {'conf':>4} {'차단사유':<15} {'가상P&L':>8} {'최대↑':>7} {'TP도달':>8}")
        for t in sorted(tp_trades, key=lambda x: -x.virtual_exit_pnl):
            lines.append(
                f"  {t.date:<12} {t.ticker:<10} {t.confidence:>4} "
                f"{t.skip_reason[:15]:<15} {t.virtual_exit_pnl:>+7.2f}% "
                f"{t.max_gain_pct:>+6.2f}% {t.virtual_exit_horizon:>8}"
            )
        missed_pnl = sum(t.virtual_exit_pnl for t in tp_trades)
        lines.append(f"  → 총 놓친 수익: {missed_pnl:+.2f}%")

    # 3. 올바른 차단 (가상 SL 도달)
    if sl_trades:
        lines.append(f"\n{'─' * 70}")
        lines.append("  3. 올바른 차단 (가상 SL 도달)")
        lines.append(f"{'─' * 70}")
        lines.append(f"  {'날짜':<12} {'종목':<10} {'conf':>4} {'차단사유':<15} {'가상P&L':>8} {'최대↓':>7}")
        for t in sorted(sl_trades, key=lambda x: x.virtual_exit_pnl):
            lines.append(
                f"  {t.date:<12} {t.ticker:<10} {t.confidence:>4} "
                f"{t.skip_reason[:15]:<15} {t.virtual_exit_pnl:>+7.2f}% "
                f"{t.max_drawdown_pct:>+6.2f}%"
            )
        saved_loss = sum(t.virtual_exit_pnl for t in sl_trades)
        lines.append(f"  → 총 회피한 손실: {saved_loss:+.2f}%")

    # 4. Confidence 구간별 분석
    lines.append(f"\n{'─' * 70}")
    lines.append("  4. Confidence 구간별 분석")
    lines.append(f"{'─' * 70}")
    bands = [(75, 77), (78, 80), (81, 85), (86, 90), (91, 100)]
    lines.append(f"  {'구간':<10} {'건수':>4} {'가상승률':>8} {'평균P&L':>9} {'평균max↑':>9}")
    for lo, hi in bands:
        band_trades = [t for t in trades if lo <= t.confidence <= hi]
        if not band_trades:
            continue
        n = len(band_trades)
        wr = len([t for t in band_trades if t.virtual_exit_type == "TP"]) / n * 100
        avg_p = sum(t.virtual_exit_pnl for t in band_trades) / n
        avg_g = sum(t.max_gain_pct for t in band_trades) / n
        lines.append(f"  {lo}-{hi:<7} {n:>4} {wr:>7.1f}% {avg_p:>+8.2f}% {avg_g:>+8.2f}%")

    # 5. 차단 사유별 분석
    lines.append(f"\n{'─' * 70}")
    lines.append("  5. 차단 사유별 분석")
    lines.append(f"{'─' * 70}")
    reason_groups: dict[str, list[ShadowTrade]] = defaultdict(list)
    for t in trades:
        reason_groups[t.skip_reason or "UNKNOWN"].append(t)
    lines.append(f"  {'사유':<25} {'건수':>4} {'가상승률':>8} {'평균P&L':>9}")
    for reason, group in sorted(reason_groups.items(), key=lambda x: -len(x[1])):
        n = len(group)
        wr = len([t for t in group if t.virtual_exit_type == "TP"]) / n * 100
        avg_p = sum(t.virtual_exit_pnl for t in group) / n
        lines.append(f"  {reason[:25]:<25} {n:>4} {wr:>7.1f}% {avg_p:>+8.2f}%")

    # 6. v67: 뉴스 카테고리별 분석
    lines.append(f"\n{'─' * 70}")
    lines.append("  6. 뉴스 카테고리별 분석")
    lines.append(f"{'─' * 70}")
    cat_groups: dict[str, list[ShadowTrade]] = defaultdict(list)
    for t in trades:
        cat_groups[t.news_type].append(t)
    lines.append(f"  {'카테고리':<22} {'건수':>4} {'가상승률':>8} {'평균P&L':>9} {'평균max↑':>9}")
    for cat, group in sorted(cat_groups.items(), key=lambda x: -len(x[1])):
        n = len(group)
        wr = len([t for t in group if t.virtual_exit_type == "TP"]) / n * 100
        avg_p = sum(t.virtual_exit_pnl for t in group) / n
        avg_g = sum(t.max_gain_pct for t in group) / n
        lines.append(f"  {cat:<22} {n:>4} {wr:>7.1f}% {avg_p:>+8.2f}% {avg_g:>+8.2f}%")

    # 7. 시간대별 분석
    lines.append(f"\n{'─' * 70}")
    lines.append("  7. 시간대별 분석 (KST)")
    lines.append(f"{'─' * 70}")
    hour_groups: dict[str, list[ShadowTrade]] = defaultdict(list)
    for t in trades:
        hour_key = f"{t.detected_hour_kst:02d}" if t.detected_hour_kst >= 0 else "??"
        hour_groups[hour_key].append(t)
    lines.append(f"  {'시간':<8} {'건수':>4} {'가상승률':>8} {'평균P&L':>9}")
    for hour, group in sorted(hour_groups.items()):
        n = len(group)
        wr = len([t for t in group if t.virtual_exit_type == "TP"]) / n * 100
        avg_p = sum(t.virtual_exit_pnl for t in group) / n
        lines.append(f"  {hour}:00   {n:>4} {wr:>7.1f}% {avg_p:>+8.2f}%")

    # 8. Flat/stale 의심 건
    flat_trades = [t for t in trades if t.flat_price]
    if flat_trades:
        lines.append(f"\n{'─' * 70}")
        lines.append("  8. Flat-price / stale 의심 건")
        lines.append(f"{'─' * 70}")
        lines.append("  동일 가격이 여러 horizon에서 반복된 건입니다. after-close/VTS 환경이면 기회비용 해석을 보수적으로 해야 합니다.")
        lines.append(f"  {'날짜':<12} {'종목':<10} {'conf':>4} {'차단사유':<20} {'source':<12}")
        for t in flat_trades:
            sources = ",".join(t.price_sources) if t.price_sources else "-"
            lines.append(
                f"  {t.date:<12} {t.ticker:<10} {t.confidence:>4} "
                f"{t.skip_reason[:20]:<20} {sources[:12]:<12}"
            )

    # 9. 개별 트레이드 상세
    lines.append(f"\n{'─' * 70}")
    lines.append("  9. 개별 트레이드 상세")
    lines.append(f"{'─' * 70}")
    for t in trades:
        lines.append(f"\n  [{t.event_id}]")
        hour_label = f"{t.detected_hour_kst:02d}:00 KST" if t.detected_hour_kst >= 0 else "unknown hour"
        lines.append(f"    {t.date} | {t.ticker} | conf={t.confidence} | {t.bucket} | {t.news_type} | {hour_label}")
        lines.append(f"    헤드라인: {t.headline}")
        lines.append(f"    차단사유: {t.skip_reason}")
        lines.append(f"    진입가: {t.t0_price:,.0f}")
        if t.price_sources:
            lines.append(f"    price_source: {', '.join(t.price_sources)}")
        if t.flat_price:
            lines.append("    flat-price suspect: multiple horizons repeated the same px")
        horizon_str = "  ".join(f"{h}:{r:+.2f}%" for h, r in t.returns.items())
        lines.append(f"    수익률: {horizon_str}")
        lines.append(f"    가상결과: {t.virtual_exit_type} @ {t.virtual_exit_horizon} ({t.virtual_exit_pnl:+.2f}%)")
        lines.append(f"    max↑={t.max_gain_pct:+.2f}%  max↓={t.max_drawdown_pct:+.2f}%")

    # 결론
    lines.append(f"\n{'=' * 70}")
    if virtual_win_rate > 50 and avg_pnl > 0:
        lines.append("  ⚠ ALERT: guardrail이 수익 기회를 과도하게 차단하고 있을 수 있음")
        lines.append(f"  가상 승률 {virtual_win_rate:.1f}%, 평균 P&L {avg_pnl:+.2f}%")
        lines.append("  → guardrail 조건 완화 검토 필요")
    elif virtual_win_rate < 30:
        lines.append("  ✓ guardrail이 정상 작동 중 — 차단된 시그널 대부분 손실")
    else:
        lines.append("  ◐ 혼합 결과 — 추가 데이터 수집 후 재분석 필요")
    lines.append("=" * 70)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Shadow snapshot 기회비용 분석")
    parser.add_argument("--snapshot-dir", type=Path, default=PROJECT_ROOT / "data" / "runtime" / "price_snapshots")
    parser.add_argument("--log-dir", type=Path, default=PROJECT_ROOT / "logs")
    parser.add_argument("--dates", nargs="*", help="분석할 날짜 (YYYYMMDD)")
    parser.add_argument("--tp", type=float, default=DEFAULT_TP_PCT, help="가상 TP %% (기본: 2.0)")
    parser.add_argument("--sl", type=float, default=DEFAULT_SL_PCT, help="가상 SL %% (기본: -1.5)")
    parser.add_argument("--output", type=str, help="리포트 출력 파일")
    args = parser.parse_args()

    events, decisions, snapshots = load_shadow_data(
        snapshot_dir=args.snapshot_dir,
        log_dir=args.log_dir,
        dates=args.dates,
    )

    trades = build_shadow_trades(events, snapshots, tp_pct=args.tp, sl_pct=args.sl)
    report = render_report(trades, tp_pct=args.tp, sl_pct=args.sl)

    print(report)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"\n리포트 저장: {args.output}")


if __name__ == "__main__":
    main()
