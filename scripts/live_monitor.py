#!/usr/bin/env python3
"""실시간 성과 모니터링 — 오늘 로그를 읽어 BUY/SKIP 현황 + 수익률 요약.

사용법:
    python scripts/live_monitor.py              # 1회 출력
    python scripts/live_monitor.py --watch      # 30초마다 갱신
    python scripts/live_monitor.py --telegram   # 텔레그램 전송
    python scripts/live_monitor.py --watch 15   # 15초마다 갱신
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kindshot.strategy_observability import StrategyReportConfig, classify_buy_exit


# ── 데이터 수집 ──

def _load_today_records(log_dir: Path, date_str: str | None = None) -> tuple[Path, list[dict]]:
    """오늘 로그 파일 로드."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    log_path = log_dir / f"kindshot_{date_str}.jsonl"
    if not log_path.exists():
        return log_path, []
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
    return log_path, records


def _parse_records(records: list[dict]) -> dict:
    """이벤트/결정/스냅샷 분류."""
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


# ── 모니터링 포맷 ──

def format_monitor(data: dict, date_str: str) -> str:
    events = data["events"]
    decisions = data["decisions"]
    snapshots = data["snapshots"]
    report_config = StrategyReportConfig()

    lines: list[str] = []
    w = lines.append

    now = datetime.now().strftime("%H:%M:%S")
    w(f"{'=' * 60}")
    w(f"  LIVE MONITOR: {date_str}  (updated {now})")
    w(f"{'=' * 60}")

    # 버킷 집계
    bucket_counts: dict[str, int] = defaultdict(int)
    for ev in events.values():
        bucket_counts[ev.get("bucket", "?")] += 1

    total_events = len(events)
    n_buy = sum(1 for d in decisions.values() if d.get("action") == "BUY")
    n_skip = sum(1 for d in decisions.values() if d.get("action") == "SKIP")

    w(f"  이벤트: {total_events}  |  LLM: {len(decisions)} (BUY={n_buy}, SKIP={n_skip})")
    bparts = []
    for b in ["POS_STRONG", "POS_WEAK", "NEG_STRONG", "UNKNOWN", "IGNORE"]:
        if bucket_counts[b]:
            bparts.append(f"{b}={bucket_counts[b]}")
    w(f"  버킷: {' | '.join(bparts)}")
    w("")

    # BUY 종목 실시간 현황
    buy_decisions = {eid: d for eid, d in decisions.items() if d.get("action") == "BUY"}
    if buy_decisions:
        w(f"  {'─' * 58}")
        w(f"  BUY 실시간 현황 ({n_buy}건)")
        w(f"  {'─' * 58}")
        w(f"  {'시간':<8} {'티커':<8} {'종목':<16} {'conf':>4} {'size':>4} {'t+5m':>7} {'latest':>7} {'exit':>10}")
        w(f"  {'─' * 58}")

        close_rets: list[float] = []
        win_count = 0
        total_pnl = 0.0

        for eid, dec in sorted(buy_decisions.items(), key=lambda x: x[1].get("decided_at", "")):
            ev = events.get(eid, {})
            ticker = ev.get("ticker", "?")
            corp = ev.get("corp_name", "")[:14]
            conf = dec.get("confidence", "?")
            size = dec.get("size_hint", "?")
            snaps = snapshots.get(eid, {})

            # 시간 추출
            decided_at = dec.get("decided_at", "")
            time_str = ""
            if decided_at:
                try:
                    time_str = datetime.fromisoformat(decided_at).strftime("%H:%M")
                except (ValueError, TypeError):
                    pass

            # 수익률
            t5m = _ret_pct(snaps, "t+5m")
            # 최신 수익률: close > t+30m > t+20m > t+15m > t+5m > t+2m > t+1m
            latest_ret = None
            latest_horizon = ""
            for h in ["close", "t+30m", "t+20m", "t+15m", "t+5m", "t+2m", "t+1m", "t+30s"]:
                r = _ret_pct(snaps, h)
                if r is not None:
                    latest_ret = r
                    latest_horizon = h
                    break

            if latest_ret is not None:
                close_rets.append(latest_ret)
                total_pnl += latest_ret
                if latest_ret > 0:
                    win_count += 1

            t5m_str = f"{t5m:>+6.2f}%" if t5m is not None else f"{'--':>7}"
            latest_str = f"{latest_ret:>+6.2f}%" if latest_ret is not None else f"{'--':>7}"

            # Exit type
            exit_type, exit_horizon = classify_buy_exit(ev, snaps, config=report_config)
            exit_str = ""
            if exit_type and exit_horizon:
                tag_map = {"take_profit": "TP", "stop_loss": "SL", "trailing_stop": "TS", "max_hold": "HOLD"}
                exit_str = f"{tag_map.get(exit_type, exit_type)}@{exit_horizon}"

            w(f"  {time_str:<8} {ticker:<8} {corp:<16} {conf:>4} {size:>4} {t5m_str} {latest_str} {exit_str:>10}")

        if close_rets:
            w(f"  {'─' * 58}")
            wr = win_count / len(close_rets) * 100 if close_rets else 0
            avg = total_pnl / len(close_rets)
            w(f"  승률: {win_count}/{len(close_rets)} ({wr:.0f}%)  평균: {avg:+.2f}%  합계: {total_pnl:+.2f}%")
    else:
        w("  BUY 시그널 없음")

    w("")

    # 킬스위치 / guardrail 상태
    kill_halts = sum(1 for ev in events.values() if ev.get("skip_reason") == "CONSECUTIVE_STOP_LOSS")
    midday_blocks = sum(1 for ev in events.values() if ev.get("skip_reason") == "MIDDAY_SPREAD_TOO_WIDE")
    breadth_blocks = sum(1 for ev in events.values() if ev.get("skip_reason") == "MARKET_BREADTH_RISK_OFF")
    daily_loss_blocks = sum(1 for ev in events.values() if ev.get("skip_reason") == "DAILY_LOSS_LIMIT")

    if kill_halts or midday_blocks or breadth_blocks or daily_loss_blocks:
        w(f"  {'─' * 58}")
        w("  Guardrail 현황")
        w(f"  {'─' * 58}")
        if kill_halts:
            w(f"  !! 킬스위치 발동: {kill_halts}회")
        if daily_loss_blocks:
            w(f"  !! 일일 손실 한도: {daily_loss_blocks}회")
        if midday_blocks:
            w(f"  비유동 시간대 차단: {midday_blocks}회")
        if breadth_blocks:
            w(f"  시장 breadth 차단: {breadth_blocks}회")
        w("")

    # 최근 이벤트 (마지막 5건)
    recent_events = sorted(events.values(), key=lambda x: x.get("detected_at", ""))[-5:]
    if recent_events:
        w(f"  {'─' * 58}")
        w("  최근 이벤트")
        w(f"  {'─' * 58}")
        for ev in recent_events:
            detected = ev.get("detected_at", "")
            time_str = ""
            if detected:
                try:
                    time_str = datetime.fromisoformat(detected).strftime("%H:%M:%S")
                except (ValueError, TypeError):
                    pass
            ticker = ev.get("ticker", "?")
            bucket = ev.get("bucket", "?")
            headline = ev.get("headline", "")[:30]
            w(f"  {time_str} [{bucket[:3]}] {ticker} {headline}")

    w(f"\n{'=' * 60}")
    return "\n".join(lines)


def format_telegram_monitor(data: dict, date_str: str) -> str:
    """텔레그램용 간결 포맷."""
    events = data["events"]
    decisions = data["decisions"]
    snapshots = data["snapshots"]
    report_config = StrategyReportConfig()

    lines: list[str] = []
    w = lines.append

    now = datetime.now().strftime("%H:%M")
    n_buy = sum(1 for d in decisions.values() if d.get("action") == "BUY")
    n_skip = sum(1 for d in decisions.values() if d.get("action") == "SKIP")

    w(f"<b>LIVE {date_str} {now}</b>")
    w(f"이벤트 {len(events)} | BUY {n_buy} SKIP {n_skip}")

    buy_decisions = {eid: d for eid, d in decisions.items() if d.get("action") == "BUY"}
    if buy_decisions:
        close_rets: list[float] = []
        for eid, dec in sorted(buy_decisions.items(), key=lambda x: x[1].get("decided_at", "")):
            ev = events.get(eid, {})
            ticker = ev.get("ticker", "?")
            conf = dec.get("confidence", "?")
            snaps = snapshots.get(eid, {})

            # 최신 수익률
            latest_ret = None
            for h in ["close", "t+30m", "t+20m", "t+15m", "t+5m", "t+2m", "t+1m"]:
                r = _ret_pct(snaps, h)
                if r is not None:
                    latest_ret = r
                    break

            exit_type, exit_horizon = classify_buy_exit(ev, snaps, config=report_config)
            exit_str = ""
            if exit_type and exit_horizon:
                tag_map = {"take_profit": "TP", "stop_loss": "SL", "trailing_stop": "TS", "max_hold": "HOLD"}
                exit_str = f" [{tag_map.get(exit_type, '')}@{exit_horizon}]"

            ret_str = f"{latest_ret:+.1f}%" if latest_ret is not None else "--"
            emoji = ""
            if latest_ret is not None:
                emoji = "+" if latest_ret > 0 else "-" if latest_ret < 0 else "="
                if latest_ret is not None:
                    close_rets.append(latest_ret)

            w(f"{emoji} <b>{ticker}</b> c={conf} {ret_str}{exit_str}")

        if close_rets:
            wins = sum(1 for r in close_rets if r > 0)
            avg = sum(close_rets) / len(close_rets)
            w(f"\n승률 {wins}/{len(close_rets)} 평균 {avg:+.2f}%")
    else:
        w("BUY 없음")

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
    watch_mode = "--watch" in sys.argv

    # --watch 뒤 숫자가 있으면 interval로 사용
    interval = 30
    args_clean = [a for a in sys.argv[1:] if not a.startswith("--")]
    if watch_mode:
        watch_idx = sys.argv.index("--watch")
        if watch_idx + 1 < len(sys.argv) and sys.argv[watch_idx + 1].isdigit():
            interval = int(sys.argv[watch_idx + 1])

    date_str = None
    for a in args_clean:
        if len(a) == 8 and a.isdigit():
            date_str = a
            break

    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    while True:
        log_path, records = _load_today_records(log_dir, date_str)

        if not records:
            print(f"로그 없음: {log_path}")
        else:
            data = _parse_records(records)

            if telegram_mode:
                text = format_telegram_monitor(data, date_str)
                if _send_telegram(text):
                    print(f"텔레그램 전송 완료 ({date_str})")
                else:
                    print("텔레그램 전송 실패")
            else:
                # 터미널 클리어 후 출력
                if watch_mode:
                    print("\033[2J\033[H", end="")
                print(format_monitor(data, date_str))

        if not watch_mode:
            break

        time.sleep(interval)


if __name__ == "__main__":
    main()
