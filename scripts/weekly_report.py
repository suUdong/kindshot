#!/usr/bin/env python3
"""주간 성과 리포트 — 최근 7일 일일 데이터 집계.

사용법:
    python scripts/weekly_report.py              # 최근 7일 (터미널)
    python scripts/weekly_report.py --telegram   # 텔레그램 전송
    python scripts/weekly_report.py --days 14    # 최근 14일
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kindshot.strategy_observability import StrategyReportConfig, classify_buy_exit, collect_strategy_summary


# ── 데이터 수집 ──

def _load_records(log_path: Path) -> list[dict]:
    records = []
    if not log_path.exists():
        return records
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _collect_day(log_path: Path) -> dict | None:
    """하루치 로그 파싱. 로그 없으면 None."""
    records = _load_records(log_path)
    if not records:
        return None

    events: dict[str, dict] = {}
    decisions: dict[str, dict] = {}
    snapshots: dict[str, dict[str, dict]] = defaultdict(dict)

    for rec in records:
        rtype = rec.get("type")
        eid = rec.get("event_id", "")
        if rtype == "event":
            if rec.get("skip_reason") == "DUPLICATE":
                continue
            events[eid] = rec
        elif rtype == "decision":
            decisions[eid] = rec
        elif rtype == "price_snapshot":
            horizon = rec.get("horizon", "")
            if eid and horizon:
                snapshots[eid][horizon] = rec

    return {"events": events, "decisions": decisions, "snapshots": snapshots}


def _ret_pct(snaps: dict, horizon: str) -> Optional[float]:
    ret = snaps.get(horizon, {}).get("ret_long_vs_t0")
    if ret is not None:
        return ret * 100
    return None


# ── 주간 집계 ──

def aggregate_weekly(log_dir: Path, days: int = 7) -> dict:
    """최근 N일 데이터 집계."""
    report_config = StrategyReportConfig()
    today = datetime.now()

    daily_stats: list[dict] = []
    all_buy_rets: list[float] = []
    all_exit_types: dict[str, int] = defaultdict(int)
    bucket_buy_rets: dict[str, list[float]] = defaultdict(list)
    hour_buy_rets: dict[int, list[float]] = defaultdict(list)
    hold_profile_rets: dict[str, list[float]] = defaultdict(list)
    strategy_totals: dict[str, int] = defaultdict(int)
    best_trade: dict | None = None
    worst_trade: dict | None = None
    active_days = 0

    for i in range(days):
        dt = today - timedelta(days=i)
        date_str = dt.strftime("%Y%m%d")
        log_path = log_dir / f"kindshot_{date_str}.jsonl"
        day_data = _collect_day(log_path)
        if day_data is None:
            continue

        events = day_data["events"]
        decisions = day_data["decisions"]
        snapshots = day_data["snapshots"]

        buy_decisions = {eid: d for eid, d in decisions.items() if d.get("action") == "BUY"}
        if not buy_decisions:
            daily_stats.append({
                "date": date_str, "events": len(events), "buys": 0,
                "wins": 0, "avg_ret": 0, "total_ret": 0,
            })
            active_days += 1
            continue

        active_days += 1
        day_rets: list[float] = []
        day_wins = 0

        for eid, dec in buy_decisions.items():
            ev = events.get(eid, {})
            snaps = snapshots.get(eid, {})
            close_ret = _ret_pct(snaps, "close")

            # exit type
            exit_type, exit_horizon = classify_buy_exit(ev, snaps, config=report_config)
            if exit_type:
                all_exit_types[exit_type] += 1
                # exit 시점 수익률
                exit_ret = _ret_pct(snaps, exit_horizon) if exit_horizon else close_ret
            else:
                exit_ret = close_ret

            effective_ret = exit_ret if exit_ret is not None else close_ret
            if effective_ret is None:
                continue

            day_rets.append(effective_ret)
            all_buy_rets.append(effective_ret)
            if effective_ret > 0:
                day_wins += 1

            # bucket별
            bucket = ev.get("bucket", "?")
            bucket_buy_rets[bucket].append(effective_ret)

            # 시간대별
            detected = ev.get("detected_at", "")
            if detected:
                try:
                    hour = datetime.fromisoformat(detected).hour
                    hour_buy_rets[hour].append(effective_ret)
                except (ValueError, TypeError):
                    pass

            # best/worst
            ticker = ev.get("ticker", "?")
            headline = ev.get("headline", "")[:20]
            trade_info = {"ticker": ticker, "headline": headline, "ret": effective_ret, "date": date_str, "conf": dec.get("confidence", 0)}
            if best_trade is None or effective_ret > best_trade["ret"]:
                best_trade = trade_info
            if worst_trade is None or effective_ret < worst_trade["ret"]:
                worst_trade = trade_info

        # 전략 요약
        strategy = collect_strategy_summary(events, decisions, snapshots, report_config)
        for key in ["trailing_stop_hits", "take_profit_hits", "stop_loss_hits", "max_hold_hits",
                     "kill_switch_halts", "midday_spread_blocks"]:
            strategy_totals[key] += strategy.get(key, 0)

        daily_stats.append({
            "date": date_str,
            "events": len(events),
            "buys": len(buy_decisions),
            "wins": day_wins,
            "avg_ret": sum(day_rets) / len(day_rets) if day_rets else 0,
            "total_ret": sum(day_rets),
        })

    return {
        "days": days,
        "active_days": active_days,
        "daily_stats": list(reversed(daily_stats)),  # 오래된 날짜 먼저
        "all_buy_rets": all_buy_rets,
        "all_exit_types": dict(all_exit_types),
        "bucket_buy_rets": dict(bucket_buy_rets),
        "hour_buy_rets": dict(hour_buy_rets),
        "strategy_totals": dict(strategy_totals),
        "best_trade": best_trade,
        "worst_trade": worst_trade,
    }


# ── 포맷 ──

def format_txt(agg: dict) -> str:
    lines: list[str] = []
    w = lines.append

    all_rets = agg["all_buy_rets"]
    total_buys = len(all_rets)
    wins = sum(1 for r in all_rets if r > 0)

    w(f"{'=' * 60}")
    w(f"  WEEKLY REPORT ({agg['days']}일, 활성일 {agg['active_days']}일)")
    w(f"{'=' * 60}")

    if total_buys > 0:
        avg = sum(all_rets) / total_buys
        gross_win = sum(r for r in all_rets if r > 0)
        gross_loss = abs(sum(r for r in all_rets if r < 0))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0

        w(f"  총 거래: {total_buys}건 | 승률: {wins}/{total_buys} ({wins/total_buys*100:.0f}%)")
        w(f"  평균 수익: {avg:+.2f}% | 합계: {sum(all_rets):+.2f}% | PF: {pf:.2f}")
        w(f"  최고: {max(all_rets):+.2f}% | 최저: {min(all_rets):+.2f}%")
    else:
        w("  거래 없음")

    w("")

    # 일별 요약
    w(f"  {'─' * 58}")
    w(f"  일별 요약")
    w(f"  {'─' * 58}")
    w(f"  {'날짜':<10} {'이벤트':>6} {'BUY':>4} {'승':>3} {'패':>3} {'평균':>7} {'합계':>7}")
    w(f"  {'─' * 58}")
    for ds in agg["daily_stats"]:
        date = ds["date"]
        n_ev = ds["events"]
        n_buy = ds["buys"]
        n_win = ds["wins"]
        n_loss = n_buy - n_win
        avg = ds["avg_ret"]
        total = ds["total_ret"]
        if n_buy > 0:
            w(f"  {date:<10} {n_ev:>6} {n_buy:>4} {n_win:>3} {n_loss:>3} {avg:>+6.2f}% {total:>+6.2f}%")
        else:
            w(f"  {date:<10} {n_ev:>6}    0   -   -       -       -")
    w("")

    # Exit type 분포
    exit_types = agg["all_exit_types"]
    if exit_types:
        w(f"  {'─' * 58}")
        w("  Exit Type 분포")
        w(f"  {'─' * 58}")
        tag_map = {"take_profit": "TP", "stop_loss": "SL", "trailing_stop": "TS", "max_hold": "HOLD"}
        for et, cnt in sorted(exit_types.items(), key=lambda x: -x[1]):
            label = tag_map.get(et, et)
            w(f"  {label}: {cnt}회")
        w("")

    # Bucket별 승률
    bucket_rets = agg["bucket_buy_rets"]
    if bucket_rets:
        w(f"  {'─' * 58}")
        w("  Bucket별 BUY 성과")
        w(f"  {'─' * 58}")
        for bucket, rets in sorted(bucket_rets.items()):
            bwins = sum(1 for r in rets if r > 0)
            bavg = sum(rets) / len(rets)
            w(f"  {bucket}: {bwins}/{len(rets)}승 ({bwins/len(rets)*100:.0f}%) avg={bavg:+.2f}%")
        w("")

    # 시간대별 승률
    hour_rets = agg["hour_buy_rets"]
    if hour_rets:
        w(f"  {'─' * 58}")
        w("  시간대별 BUY 성과")
        w(f"  {'─' * 58}")
        for hour in sorted(hour_rets.keys()):
            rets = hour_rets[hour]
            hwins = sum(1 for r in rets if r > 0)
            havg = sum(rets) / len(rets)
            w(f"  {hour:02d}시: {hwins}/{len(rets)}승 ({hwins/len(rets)*100:.0f}%) avg={havg:+.2f}%")
        w("")

    # 전략 동작 집계
    st = agg["strategy_totals"]
    if st:
        w(f"  {'─' * 58}")
        w("  전략 동작 주간 합계")
        w(f"  {'─' * 58}")
        w(f"  TP: {st.get('take_profit_hits', 0)} | SL: {st.get('stop_loss_hits', 0)} | TS: {st.get('trailing_stop_hits', 0)} | HOLD: {st.get('max_hold_hits', 0)}")
        w(f"  킬스위치: {st.get('kill_switch_halts', 0)} | Midday: {st.get('midday_spread_blocks', 0)}")
        w("")

    # 최고/최저 종목
    if agg["best_trade"]:
        bt = agg["best_trade"]
        w(f"  최고 종목: {bt['ticker']} {bt['ret']:+.2f}% ({bt['date']}) {bt['headline']}")
    if agg["worst_trade"]:
        wt = agg["worst_trade"]
        w(f"  최저 종목: {wt['ticker']} {wt['ret']:+.2f}% ({wt['date']}) {wt['headline']}")

    w(f"\n{'=' * 60}")
    return "\n".join(lines)


def format_telegram(agg: dict) -> str:
    lines: list[str] = []
    w = lines.append

    all_rets = agg["all_buy_rets"]
    total_buys = len(all_rets)
    wins = sum(1 for r in all_rets if r > 0)

    w(f"<b>WEEKLY ({agg['days']}일, 활성 {agg['active_days']}일)</b>")

    if total_buys > 0:
        avg = sum(all_rets) / total_buys
        gross_win = sum(r for r in all_rets if r > 0)
        gross_loss = abs(sum(r for r in all_rets if r < 0))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0

        w(f"거래 {total_buys} | 승률 {wins}/{total_buys} ({wins/total_buys*100:.0f}%)")
        w(f"평균 {avg:+.2f}% | 합계 {sum(all_rets):+.2f}% | PF {pf:.2f}")
    else:
        w("거래 없음")

    # 일별 한줄 요약
    w("")
    w("<b>일별</b>")
    for ds in agg["daily_stats"]:
        if ds["buys"] > 0:
            wr = ds["wins"] / ds["buys"] * 100
            w(f"{ds['date']}: {ds['buys']}건 {ds['wins']}승 ({wr:.0f}%) {ds['total_ret']:+.2f}%")
        else:
            w(f"{ds['date']}: 거래없음 (이벤트 {ds['events']})")

    # Exit type
    exit_types = agg["all_exit_types"]
    if exit_types:
        tag_map = {"take_profit": "TP", "stop_loss": "SL", "trailing_stop": "TS", "max_hold": "HOLD"}
        parts = [f"{tag_map.get(et, et)}:{cnt}" for et, cnt in sorted(exit_types.items(), key=lambda x: -x[1])]
        w(f"\nExit: {' '.join(parts)}")

    # 시간대별 top 3
    hour_rets = agg["hour_buy_rets"]
    if hour_rets:
        sorted_hours = sorted(hour_rets.items(), key=lambda x: -(sum(1 for r in x[1] if r > 0) / len(x[1]) if x[1] else 0))
        top3 = sorted_hours[:3]
        parts = []
        for h, rets in top3:
            hw = sum(1 for r in rets if r > 0)
            parts.append(f"{h:02d}시({hw}/{len(rets)})")
        w(f"시간대 top: {' '.join(parts)}")

    # 최고/최저
    if agg["best_trade"]:
        bt = agg["best_trade"]
        w(f"\n최고: <b>{bt['ticker']}</b> {bt['ret']:+.1f}% ({bt['date']})")
    if agg["worst_trade"]:
        wt = agg["worst_trade"]
        w(f"최저: <b>{wt['ticker']}</b> {wt['ret']:+.1f}% ({wt['date']})")

    return "\n".join(lines)


# ── 텔레그램 전송 ──

def _send_telegram(text: str) -> bool:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        print("TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 환경변수 필요")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return bool(result.get("ok"))
    except Exception as e:
        print(f"텔레그램 전송 실패: {e}")
        return False


# ── CLI ──

def main() -> None:
    log_dir = Path("/opt/kindshot/logs")
    if not log_dir.exists():
        log_dir = Path("logs")

    telegram_mode = "--telegram" in sys.argv
    days = 7
    if "--days" in sys.argv:
        idx = sys.argv.index("--days")
        if idx + 1 < len(sys.argv) and sys.argv[idx + 1].isdigit():
            days = int(sys.argv[idx + 1])

    agg = aggregate_weekly(log_dir, days=days)

    if telegram_mode:
        text = format_telegram(agg)
        if _send_telegram(text):
            print(f"주간 리포트 텔레그램 전송 완료 ({days}일)")
        else:
            print("텔레그램 전송 실패")
    else:
        print(format_txt(agg))


if __name__ == "__main__":
    main()
