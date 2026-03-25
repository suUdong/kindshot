#!/usr/bin/env python3
"""장 마감 후 daily report — 이벤트별 티커 + 시간대별 수익률 요약.

사용법:
    python deploy/daily_report.py              # 오늘 (txt)
    python deploy/daily_report.py 20260311     # 특정 날짜 (txt)
    python deploy/daily_report.py --telegram   # 오늘 (텔레그램 전송)
    python deploy/daily_report.py --telegram 20260311
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kindshot.strategy_observability import (
    StrategyReportConfig,
    classify_buy_exit,
    collect_strategy_summary,
)


# ── 데이터 수집 ──

def _load_records(log_path: Path) -> list[dict]:
    records = []
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


def _collect(log_path: Path) -> dict:
    """로그 파일에서 이벤트/결정/스냅샷 수집."""
    records = _load_records(log_path)

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

    bucket_counts = defaultdict(int)
    for ev in events.values():
        bucket_counts[ev.get("bucket", "?")] += 1

    hour_dist = defaultdict(int)
    for ev in events.values():
        detected = ev.get("detected_at", "")
        if detected:
            try:
                dt = datetime.fromisoformat(detected)
                hour_dist[dt.hour] += 1
            except (ValueError, TypeError):
                pass

    report_config = StrategyReportConfig()
    strategy_summary = collect_strategy_summary(events, decisions, snapshots, report_config)

    return {
        "events": events,
        "decisions": decisions,
        "snapshots": snapshots,
        "bucket_counts": bucket_counts,
        "hour_dist": hour_dist,
        "report_config": report_config,
        "strategy_summary": strategy_summary,
    }


def _hold_profile_text(summary: dict) -> str:
    parts = []
    for label, count in sorted(summary["hold_profile_breakdown"].items()):
        parts.append(f"{label}:{count}")
    return ", ".join(parts) if parts else "-"


def _ret_pct(snaps: dict, horizon: str, key: str = "ret_long_vs_t0") -> Optional[float]:
    ret = snaps.get(horizon, {}).get(key)
    if ret is not None:
        return ret * 100
    return None


def _exit_tag(event: dict, snaps: dict, report_config: StrategyReportConfig) -> Optional[str]:
    exit_type, horizon = classify_buy_exit(event, snaps, config=report_config)
    if exit_type is None or horizon is None:
        return None
    if exit_type == "take_profit":
        return f"TP@{horizon}"
    if exit_type == "stop_loss":
        return f"SL@{horizon}"
    if exit_type == "trailing_stop":
        return f"TRAIL@{horizon}"
    if exit_type == "max_hold":
        return f"HOLD@{horizon}"
    return None


def _tp_sl_stats(events: dict, buy_eids: list[str], snapshots: dict, report_config: StrategyReportConfig) -> dict:
    """BUY 이벤트들의 exit-type 통계 + exit type별 수익률."""
    tp_count = 0
    sl_count = 0
    trailing_count = 0
    hold_count = 0
    neither = 0
    exit_rets: dict[str, list[float]] = {"tp": [], "sl": [], "trail": [], "hold": [], "neither": []}
    for eid in buy_eids:
        ev = events.get(eid, {})
        snaps = snapshots.get(eid, {})
        exit_type, exit_horizon = classify_buy_exit(ev, snaps, config=report_config)
        # exit 시점 수익률
        exit_ret = _ret_pct(snaps, exit_horizon) if exit_horizon else _ret_pct(snaps, "close")
        if exit_type == "take_profit":
            tp_count += 1
            if exit_ret is not None:
                exit_rets["tp"].append(exit_ret)
        elif exit_type == "stop_loss":
            sl_count += 1
            if exit_ret is not None:
                exit_rets["sl"].append(exit_ret)
        elif exit_type == "trailing_stop":
            trailing_count += 1
            if exit_ret is not None:
                exit_rets["trail"].append(exit_ret)
        elif exit_type == "max_hold":
            hold_count += 1
            if exit_ret is not None:
                exit_rets["hold"].append(exit_ret)
        else:
            neither += 1
            if exit_ret is not None:
                exit_rets["neither"].append(exit_ret)
    return {
        "tp": tp_count,
        "sl": sl_count,
        "trail": trailing_count,
        "hold": hold_count,
        "neither": neither,
        "total": len(buy_eids),
        "exit_rets": exit_rets,
    }


def _false_negative_summary(snapshots: dict) -> list[dict]:
    """SKIP 종목 중 수익이 발생한 false negative 식별."""
    false_negs = []
    for eid, snaps in snapshots.items():
        if not eid.startswith("skip_"):
            continue
        close_ret = _ret_pct(snaps, "close")
        t5m_ret = _ret_pct(snaps, "t+5m")
        best_ret = max(
            (r for r in [_ret_pct(snaps, h) for h in ["t+1m", "t+2m", "t+5m", "t+30m", "close"]] if r is not None),
            default=None,
        )
        if best_ret is not None and best_ret > 0.5:
            false_negs.append({
                "event_id": eid.replace("skip_", ""),
                "best_ret": best_ret,
                "close_ret": close_ret,
                "t5m_ret": t5m_ret,
            })
    false_negs.sort(key=lambda x: -(x["best_ret"] or 0))
    return false_negs


# ── TXT 포맷 (파일/터미널용) ──

def format_txt(log_path: Path, data: dict) -> str:
    events = data["events"]
    decisions = data["decisions"]
    snapshots = data["snapshots"]
    bucket_counts = data["bucket_counts"]
    hour_dist = data["hour_dist"]
    report_config = data["report_config"]
    strategy_summary = data["strategy_summary"]

    lines = []
    w = lines.append

    w(f"{'=' * 72}")
    w(f"  DAILY REPORT: {log_path.stem}")
    w(f"{'=' * 72}")
    w(f"  총 이벤트: {len(events)}건")

    parts = []
    for b in ["POS_STRONG", "POS_WEAK", "NEG_STRONG", "NEG_WEAK", "IGNORE", "UNKNOWN"]:
        if bucket_counts[b]:
            parts.append(f"{b}={bucket_counts[b]}")
    w(f"  버킷: {' | '.join(parts)}")

    n_buy = sum(1 for d in decisions.values() if d.get("action") == "BUY")
    n_skip = sum(1 for d in decisions.values() if d.get("action") == "SKIP")
    w(f"  LLM 판단: {len(decisions)}건 (BUY={n_buy}, SKIP={n_skip})")
    w("")

    # BUY 성과
    buy_decisions = {eid: d for eid, d in decisions.items() if d.get("action") == "BUY"}
    if buy_decisions:
        w(f"  {'─' * 70}")
        w(f"  BUY 판단 성과")
        w(f"  {'─' * 70}")
        w(f"  {'티커':<8} {'헤드라인':<28} {'conf':>4} {'t0':>8} {'t+1m':>7} {'t+5m':>7} {'t+30m':>7} {'close':>7}")
        w(f"  {'─' * 70}")

        close_rets = []
        for eid, dec in sorted(buy_decisions.items(), key=lambda x: x[1].get("decided_at", "")):
            ev = events.get(eid, {})
            ticker = ev.get("ticker", "?")
            headline = ev.get("headline", "")[:26]
            conf = dec.get("confidence", "?")
            snaps = snapshots.get(eid, {})

            t0_px = snaps.get("t0", {}).get("px")
            cols = [f"{t0_px:>8,.0f}" if t0_px else f"{'N/A':>8}"]

            for h in ["t+1m", "t+5m", "t+30m", "close"]:
                r = _ret_pct(snaps, h)
                if r is not None:
                    cols.append(f"{r:>+6.2f}%")
                    if h == "close":
                        close_rets.append(r)
                else:
                    cols.append(f"{'N/A':>7}")

            exit_tag = _exit_tag(ev, snaps, report_config) or ""
            suffix = f" [{exit_tag}]" if exit_tag else ""
            w(f"  {ticker:<8} {headline:<28} {conf:>4} {' '.join(cols)}{suffix}")

        if close_rets:
            w(f"  {'─' * 70}")
            wins = [r for r in close_rets if r > 0]
            avg = sum(close_rets) / len(close_rets)
            w(f"  승률: {len(wins)}/{len(close_rets)} ({len(wins)/len(close_rets)*100:.0f}%)  평균: {avg:+.2f}%  최고: {max(close_rets):+.2f}%  최저: {min(close_rets):+.2f}%")
        w("")

    # NEG_STRONG
    neg_tracked = {
        eid: ev for eid, ev in events.items()
        if ev.get("bucket") == "NEG_STRONG" and eid in snapshots
    }
    if neg_tracked:
        w(f"  {'─' * 70}")
        w(f"  NEG_STRONG (SHORT_WATCH) 추적")
        w(f"  {'─' * 70}")
        w(f"  {'티커':<8} {'헤드라인':<28} {'t0':>8} {'t+1m':>7} {'t+5m':>7} {'t+30m':>7} {'close':>7}")
        w(f"  {'─' * 70}")

        for eid, ev in sorted(neg_tracked.items(), key=lambda x: x[1].get("detected_at", "")):
            ticker = ev.get("ticker", "?")
            headline = ev.get("headline", "")[:26]
            snaps = snapshots.get(eid, {})

            t0_px = snaps.get("t0", {}).get("px")
            cols = [f"{t0_px:>8,.0f}" if t0_px else f"{'N/A':>8}"]

            for h in ["t+1m", "t+5m", "t+30m", "close"]:
                r = _ret_pct(snaps, h, key="ret_short_vs_t0")
                if r is not None:
                    cols.append(f"{r:>+6.2f}%")
                else:
                    cols.append(f"{'N/A':>7}")

            w(f"  {ticker:<8} {headline:<28} {' '.join(cols)}")
        w("")

    # 시간대별
    if hour_dist:
        w(f"  {'─' * 70}")
        w(f"  시간대별 이벤트 분포")
        w(f"  {'─' * 70}")
        for h in sorted(hour_dist):
            bar = "#" * min(hour_dist[h], 50)
            w(f"  {h:02d}시  {bar} {hour_dist[h]}")
        w("")

    w(f"  {'─' * 70}")
    w("  전략 동작 현황")
    w(f"  {'─' * 70}")
    hold_text = _hold_profile_text(strategy_summary)
    w(f"  Trailing Stop 발동: {strategy_summary['trailing_stop_hits']}회")
    w(f"  Take Profit 발동: {strategy_summary['take_profit_hits']}회")
    w(f"  Stop Loss 발동: {strategy_summary['stop_loss_hits']}회")
    w(f"  Max Hold 발동: {strategy_summary['max_hold_hits']}회")
    w(f"  보유시간 차등 적용: {strategy_summary['hold_profile_applied']}건 ({hold_text})")
    w(f"  킬스위치 halt: {strategy_summary['kill_switch_halts']}회")
    w(
        "  시간대별 guardrail: "
        f"midday_spread={strategy_summary['midday_spread_blocks']}, "
        f"market_close_cutoff={strategy_summary['market_close_cutoffs']}"
    )
    w(f"  체결/해지 NEG 재분류: {strategy_summary['contract_cancellation_negs']}건")
    w(f"  SKIP 추적 스케줄: {strategy_summary['skip_tracking_scheduled']}건")
    w("")

    w(f"{'=' * 72}")
    return "\n".join(lines)


# ── 텔레그램 포맷 (HTML) ──

def _bucket_short(name: str) -> str:
    """버킷명 축약."""
    return {
        "POS_STRONG": "P+", "POS_WEAK": "Pw",
        "NEG_STRONG": "N-", "NEG_WEAK": "Nw",
        "IGNORE": "IGN", "UNKNOWN": "UNK",
    }.get(name, name)


def _ret_emoji(r: Optional[float]) -> str:
    if r is None:
        return ""
    if r >= 2.0:
        return "🟢"
    if r >= 0:
        return "🔵"
    if r >= -1.0:
        return "🟡"
    return "🔴"


def _compact_rets(snaps: dict, key: str = "ret_long_vs_t0") -> str:
    """수익률을 한 줄로 압축. N/A만 있으면 빈 문자열."""
    parts = []
    for h in ["t+5m", "t+30m", "close"]:
        r = _ret_pct(snaps, h, key=key)
        if r is not None:
            parts.append(f"{h.replace('t+', '')}:{r:+.1f}%")
    return " ".join(parts) if parts else "수익률 N/A"


def format_telegram(log_path: Path, data: dict) -> str:
    events = data["events"]
    decisions = data["decisions"]
    snapshots = data["snapshots"]
    bucket_counts = data["bucket_counts"]
    hour_dist = data["hour_dist"]
    report_config = data["report_config"]
    strategy_summary = data["strategy_summary"]

    lines = []
    w = lines.append

    date_label = log_path.stem.replace("kindshot_", "")

    # 헤더
    n_buy = sum(1 for d in decisions.values() if d.get("action") == "BUY")
    n_skip = sum(1 for d in decisions.values() if d.get("action") == "SKIP")
    w(f"📊 <b>{date_label} Daily</b>")
    w(f"이벤트 {len(events)} | LLM {len(decisions)} (BUY {n_buy} / SKIP {n_skip})")

    # 버킷 요약 (한 줄)
    bparts = []
    for b in ["POS_STRONG", "POS_WEAK", "NEG_STRONG", "NEG_WEAK", "IGNORE", "UNKNOWN"]:
        if bucket_counts[b]:
            bparts.append(f"{_bucket_short(b)}:{bucket_counts[b]}")
    w(" ".join(bparts))

    # BUY 성과
    buy_decisions = {eid: d for eid, d in decisions.items() if d.get("action") == "BUY"}
    if buy_decisions:
        w("")
        w("💰 <b>BUY 성과</b>")

        close_rets = []
        for eid, dec in sorted(buy_decisions.items(), key=lambda x: x[1].get("decided_at", "")):
            ev = events.get(eid, {})
            ticker = ev.get("ticker", "?")
            headline = ev.get("headline", "")[:24]
            conf = dec.get("confidence", "?")
            size = dec.get("size_hint", "?")
            snaps = snapshots.get(eid, {})

            close_r = _ret_pct(snaps, "close")
            emoji = _ret_emoji(close_r)
            if close_r is not None:
                close_rets.append(close_r)

            exit_tag = _exit_tag(ev, snaps, report_config) or ""
            if exit_tag:
                exit_tag = f" [{exit_tag}]"

            w(f"{emoji}<b>{ticker}</b> {headline}")
            w(f"  c={conf}/{size} | {_compact_rets(snaps)}{exit_tag}")

        if close_rets:
            wins = [r for r in close_rets if r > 0]
            avg = sum(close_rets) / len(close_rets)
            gross_win = sum(r for r in close_rets if r > 0)
            gross_loss = abs(sum(r for r in close_rets if r < 0))
            pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
            w(f"\n📈 승률 {len(wins)}/{len(close_rets)} ({len(wins)/len(close_rets)*100:.0f}%) 평균 {avg:+.2f}% PF={pf:.2f}")

        # TP/SL 통계 + exit type별 평균 수익률
        tpsl = _tp_sl_stats(events, list(buy_decisions.keys()), snapshots, report_config)
        if tpsl["total"] > 0:
            exit_parts = []
            for label, key in [("TP", "tp"), ("SL", "sl"), ("TS", "trail"), ("HOLD", "hold")]:
                cnt = tpsl[key]
                rets = tpsl["exit_rets"][key]
                if cnt > 0 and rets:
                    avg_r = sum(rets) / len(rets)
                    exit_parts.append(f"{label}:{cnt}({avg_r:+.1f}%)")
                elif cnt > 0:
                    exit_parts.append(f"{label}:{cnt}")
            if tpsl["neither"] > 0:
                exit_parts.append(f"?:{tpsl['neither']}")
            w("🎯 " + " ".join(exit_parts))

        # confidence 구간별 승률
        conf_buckets: dict[str, list[float]] = {"90+": [], "80-89": [], "70-79": [], "65-69": []}
        for eid, dec in buy_decisions.items():
            c = dec.get("confidence", 0)
            cr = _ret_pct(snapshots.get(eid, {}), "close")
            if cr is None:
                continue
            if c >= 90:
                conf_buckets["90+"].append(cr)
            elif c >= 80:
                conf_buckets["80-89"].append(cr)
            elif c >= 70:
                conf_buckets["70-79"].append(cr)
            elif c >= 65:
                conf_buckets["65-69"].append(cr)
        active_buckets = {k: v for k, v in conf_buckets.items() if v}
        if active_buckets:
            w("")
            w("📊 <b>구간별 성과</b>")
            for label, rets in active_buckets.items():
                wins = sum(1 for r in rets if r > 0)
                avg = sum(rets) / len(rets)
                w(f"  c={label}: {wins}/{len(rets)}승 ({wins/len(rets)*100:.0f}%) avg={avg:+.1f}%")

    # SKIP 요약 (reason별 집계)
    skip_decisions = {eid: d for eid, d in decisions.items() if d.get("action") == "SKIP"}
    if skip_decisions:
        w("")
        w(f"⏭ <b>SKIP {len(skip_decisions)}건</b>")
        # 상위 5건만 표시
        for eid, dec in sorted(skip_decisions.items(), key=lambda x: -x[1].get("confidence", 0))[:5]:
            ev = events.get(eid, {})
            ticker = ev.get("ticker", "?")
            headline = ev.get("headline", "")[:20]
            conf = dec.get("confidence", "?")
            reason = dec.get("reason", "")[:30]
            w(f"  {ticker} c={conf} {headline}")
            if reason:
                w(f"    → {reason}")

    # NEG_STRONG (티커별 dedupe, 상위 10건)
    neg_tracked = {
        eid: ev for eid, ev in events.items()
        if ev.get("bucket") == "NEG_STRONG" and eid in snapshots
    }
    if neg_tracked:
        w("")
        w(f"🔻 <b>NEG 추적</b> ({len(neg_tracked)}건)")
        seen_tickers: set[str] = set()
        shown = 0
        for eid, ev in sorted(neg_tracked.items(), key=lambda x: x[1].get("detected_at", "")):
            ticker = ev.get("ticker", "?")
            if ticker in seen_tickers:
                continue
            seen_tickers.add(ticker)
            headline = ev.get("headline", "")[:24]
            snaps = snapshots.get(eid, {})
            rets = _compact_rets(snaps, key="ret_short_vs_t0")
            w(f"  <b>{ticker}</b> {headline}")
            w(f"    {rets}")
            shown += 1
            if shown >= 10:
                remaining = len(set(ev.get("ticker", "") for ev in neg_tracked.values())) - shown
                if remaining > 0:
                    w(f"  ...외 {remaining}종목")
                break

    # 피크 시간 (1줄)
    if hour_dist:
        top3 = sorted(hour_dist.items(), key=lambda x: -x[1])[:3]
        peak = " ".join(f"{h:02d}시({c})" for h, c in top3)
        w(f"\n⏰ 피크: {peak}")

    hold_text = _hold_profile_text(strategy_summary)
    w("")
    w("🧠 <b>전략 현황</b>")
    w(
        "TS:{trailing} TP:{tp} SL:{sl} HoldExit:{hold_exit}".format(
            trailing=strategy_summary["trailing_stop_hits"],
            tp=strategy_summary["take_profit_hits"],
            sl=strategy_summary["stop_loss_hits"],
            hold_exit=strategy_summary["max_hold_hits"],
        )
    )
    w(
        "HoldProfile:{applied} ({hold_text})".format(
            applied=strategy_summary["hold_profile_applied"],
            hold_text=hold_text,
        )
    )
    w(
        "KillSwitch:{halt} Midday:{midday} CloseCut:{close_cut}".format(
            halt=strategy_summary["kill_switch_halts"],
            midday=strategy_summary["midday_spread_blocks"],
            close_cut=strategy_summary["market_close_cutoffs"],
        )
    )
    w(
        "CancelNEG:{cancel_neg} SkipTrack:{skip_track}".format(
            cancel_neg=strategy_summary["contract_cancellation_negs"],
            skip_track=strategy_summary["skip_tracking_scheduled"],
        )
    )

    # False Negative 분석: SKIP했지만 수익이 발생한 종목
    false_negs = _false_negative_summary(snapshots)
    if false_negs:
        w("")
        w(f"⚠️ <b>False Negative</b> ({len(false_negs)}건)")
        for fn in false_negs[:5]:
            orig_eid = fn["event_id"]
            ev = events.get(orig_eid, {})
            ticker = ev.get("ticker", "?")
            headline = ev.get("headline", "")[:20]
            best = fn["best_ret"]
            w(f"  {ticker} +{best:.1f}% {headline}")

    return "\n".join(lines)


# ── 텔레그램 전송 ──

def send_telegram(text: str, bot_token: str, chat_id: str) -> bool:
    """텔레그램 Bot API로 메시지 전송. 외부 의존성 없음."""
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
            if result.get("ok"):
                print(f"텔레그램 전송 완료 (chat_id={chat_id})")
                return True
            else:
                print(f"텔레그램 API 에러: {result}")
                return False
    except Exception as e:
        print(f"텔레그램 전송 실패: {e}")
        return False


# ── CLI ──

def main() -> None:
    log_dir = Path("/opt/kindshot/logs")
    if not log_dir.exists():
        log_dir = Path("logs")

    telegram_mode = "--telegram" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args:
        date_str = args[0]
    else:
        date_str = datetime.now().strftime("%Y%m%d")

    log_path = log_dir / f"kindshot_{date_str}.jsonl"

    if not log_path.exists():
        print(f"로그 파일 없음: {log_path}")
        return

    data = _collect(log_path)

    if telegram_mode:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not bot_token or not chat_id:
            print("TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 환경변수 필요")
            sys.exit(1)

        text = format_telegram(log_path, data)
        send_telegram(text, bot_token, chat_id)
    else:
        print(format_txt(log_path, data))


if __name__ == "__main__":
    main()
