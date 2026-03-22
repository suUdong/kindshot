#!/usr/bin/env python3
"""Replay simulation — 과거 로그로 현재 전략의 가상 수익률 계산.

두 가지 모드:
1. --log-replay: 기존 runtime logs에서 event+decision+snapshot을 읽어 수익률 분석
2. --reclassify: 과거 뉴스를 현재 bucket 키워드로 재분류하여 놓친 시그널 분석

사용법:
    python scripts/replay_sim.py                    # 전체 날짜 log replay
    python scripts/replay_sim.py --date 20260318    # 특정 날짜
    python scripts/replay_sim.py --reclassify       # 뉴스 재분류 모드
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional


# ── 데이터 로딩 ──

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


def find_news_files(news_dir: Path, date_filter: str = "") -> list[Path]:
    pattern = f"{date_filter}*.jsonl" if date_filter else "*.jsonl"
    return sorted(news_dir.glob(pattern))


# ── Log Replay 모드 ──

def replay_from_logs(log_dir: Path, snapshot_dir: Path, date_filter: str = "") -> dict:
    """runtime logs에서 event/decision/snapshot을 읽어 수익률 분석."""
    log_files = find_log_files(log_dir, date_filter)
    if not log_files:
        print(f"로그 파일 없음: {log_dir}/kindshot_{date_filter}*.jsonl")
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

    # snapshot 파일도 로드
    if snapshot_dir.exists():
        snap_files = sorted(snapshot_dir.glob(f"{date_filter}*.jsonl" if date_filter else "*.jsonl"))
        for sf in snap_files:
            for rec in load_jsonl(sf):
                eid = rec.get("event_id", "")
                h = rec.get("horizon", "")
                if eid and h:
                    all_snapshots[eid][h] = rec

    # BUY 결정 분석
    buy_decisions = {eid: d for eid, d in all_decisions.items() if d.get("action") == "BUY"}
    skip_decisions = {eid: d for eid, d in all_decisions.items() if d.get("action") == "SKIP"}

    buy_results = []
    for eid, dec in buy_decisions.items():
        ev = all_events.get(eid, {})
        snaps = all_snapshots.get(eid, {})
        conf = dec.get("confidence", 0)
        size = dec.get("size_hint", "?")
        ticker = ev.get("ticker", "?")
        headline = ev.get("headline", "")[:40]
        bucket = ev.get("bucket", "?")

        rets = {}
        for h in ["t+30s", "t+1m", "t+2m", "t+5m", "t+30m", "close"]:
            snap = snaps.get(h, {})
            r = snap.get("ret_long_vs_t0")
            if r is not None:
                rets[h] = r * 100

        # TP/SL 판정
        tp_pct, sl_pct = 1.5, -1.0
        exit_type = None
        exit_horizon = None
        for h in ["t+30s", "t+1m", "t+2m", "t+5m", "t+30m", "close"]:
            r = rets.get(h)
            if r is None:
                continue
            if r >= tp_pct and exit_type is None:
                exit_type = "TP"
                exit_horizon = h
            elif r <= sl_pct and exit_type is None:
                exit_type = "SL"
                exit_horizon = h

        close_ret = rets.get("close")

        buy_results.append({
            "event_id": eid,
            "ticker": ticker,
            "headline": headline,
            "bucket": bucket,
            "confidence": conf,
            "size_hint": size,
            "rets": rets,
            "close_ret": close_ret,
            "exit_type": exit_type,
            "exit_horizon": exit_horizon,
        })

    return {
        "total_events": len(all_events),
        "bucket_counts": dict(bucket_counts),
        "skip_counts": dict(skip_counts),
        "total_decisions": len(all_decisions),
        "buy_count": len(buy_decisions),
        "skip_count": len(skip_decisions),
        "buy_results": buy_results,
        "log_files": [str(f) for f in log_files],
    }


# ── Reclassify 모드 ──

def reclassify_news(news_dir: Path, date_filter: str = "") -> dict:
    """과거 뉴스를 현재 bucket 키워드로 재분류."""
    # kindshot 패키지 import (프로젝트 루트에서 실행 가정)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from kindshot.bucket import classify

    news_files = find_news_files(news_dir, date_filter)
    if not news_files:
        print(f"뉴스 파일 없음: {news_dir}")
        return {}

    bucket_counts: dict[str, int] = defaultdict(int)
    ticker_news: dict[str, list[dict]] = defaultdict(list)
    pos_strong_headlines: list[dict] = []
    pos_weak_headlines: list[dict] = []

    total = 0
    for nf in news_files:
        date_str = nf.stem
        for rec in load_jsonl(nf):
            tickers = rec.get("tickers", [])
            if not tickers:
                continue
            title = rec.get("title", "")
            if not title:
                continue

            result = classify(title)
            bucket_counts[result.bucket.value] += 1
            total += 1

            entry = {
                "date": date_str,
                "title": title,
                "tickers": tickers,
                "bucket": result.bucket.value,
                "keyword_hits": result.keyword_hits,
            }

            if result.bucket.value == "POS_STRONG":
                pos_strong_headlines.append(entry)
            elif result.bucket.value == "POS_WEAK":
                pos_weak_headlines.append(entry)

            for t in tickers:
                ticker_news[t].append(entry)

    return {
        "total_news": total,
        "bucket_counts": dict(bucket_counts),
        "pos_strong_count": len(pos_strong_headlines),
        "pos_weak_count": len(pos_weak_headlines),
        "pos_strong_sample": pos_strong_headlines[:20],
        "pos_weak_sample": pos_weak_headlines[:20],
        "top_tickers": sorted(
            ((t, len(news)) for t, news in ticker_news.items()),
            key=lambda x: -x[1],
        )[:20],
        "news_files": [str(f) for f in news_files],
    }


# ── 출력 포맷 ──

def print_replay_report(data: dict) -> None:
    if not data:
        return

    print("=" * 70)
    print("  REPLAY SIMULATION REPORT")
    print("=" * 70)
    print(f"  로그 파일: {len(data.get('log_files', []))}개")
    print(f"  총 이벤트: {data['total_events']}건")
    print(f"  LLM 판단: {data['total_decisions']}건 (BUY={data['buy_count']}, SKIP={data['skip_count']})")
    print()

    # 버킷 분포
    print("  [버킷 분포]")
    for b in ["POS_STRONG", "POS_WEAK", "NEG_STRONG", "NEG_WEAK", "IGNORE", "UNKNOWN"]:
        cnt = data["bucket_counts"].get(b, 0)
        if cnt:
            print(f"    {b}: {cnt}")
    print()

    # Skip 사유
    if data["skip_counts"]:
        print("  [Skip 사유 상위]")
        for reason, cnt in sorted(data["skip_counts"].items(), key=lambda x: -x[1])[:10]:
            print(f"    {reason}: {cnt}")
        print()

    # BUY 성과
    buys = data["buy_results"]
    if buys:
        print(f"  [BUY 성과] ({len(buys)}건)")
        print(f"  {'티커':<8} {'conf':>4} {'size':>4} {'t+1m':>7} {'t+5m':>7} {'t+30m':>7} {'close':>7} {'exit':>8} 헤드라인")
        print(f"  {'-' * 70}")

        close_rets = []
        conf_buckets: dict[str, list[float]] = {"90+": [], "80-89": [], "70-79": [], "65-69": [], "<65": []}
        tp_count = sl_count = neither = 0

        for b in buys:
            t1m = b["rets"].get("t+1m")
            t5m = b["rets"].get("t+5m")
            t30m = b["rets"].get("t+30m")
            close = b["close_ret"]

            cols = []
            for v in [t1m, t5m, t30m, close]:
                cols.append(f"{v:>+6.2f}%" if v is not None else f"{'N/A':>7}")

            exit_str = f"{b['exit_type']}@{b['exit_horizon']}" if b["exit_type"] else "-"
            print(f"  {b['ticker']:<8} {b['confidence']:>4} {b['size_hint']:>4} {' '.join(cols)} {exit_str:>8} {b['headline'][:30]}")

            if close is not None:
                close_rets.append(close)
                c = b["confidence"]
                if c >= 90:
                    conf_buckets["90+"].append(close)
                elif c >= 80:
                    conf_buckets["80-89"].append(close)
                elif c >= 70:
                    conf_buckets["70-79"].append(close)
                elif c >= 65:
                    conf_buckets["65-69"].append(close)
                else:
                    conf_buckets["<65"].append(close)

            if b["exit_type"] == "TP":
                tp_count += 1
            elif b["exit_type"] == "SL":
                sl_count += 1
            else:
                neither += 1

        print()
        if close_rets:
            wins = [r for r in close_rets if r > 0]
            avg = sum(close_rets) / len(close_rets)
            print(f"  승률: {len(wins)}/{len(close_rets)} ({len(wins)/len(close_rets)*100:.0f}%)")
            print(f"  평균: {avg:+.2f}%  최고: {max(close_rets):+.2f}%  최저: {min(close_rets):+.2f}%")
            print(f"  TP: {tp_count}  SL: {sl_count}  미도달: {neither}")

        print()
        print("  [Confidence 구간별 성과]")
        for label, rets in conf_buckets.items():
            if not rets:
                continue
            w = sum(1 for r in rets if r > 0)
            a = sum(rets) / len(rets)
            print(f"    c={label}: {w}/{len(rets)}승 ({w/len(rets)*100:.0f}%) avg={a:+.2f}%")
    else:
        print("  BUY 건 없음")

    print()
    print("=" * 70)


def print_reclassify_report(data: dict) -> None:
    if not data:
        return

    print("=" * 70)
    print("  RECLASSIFY REPORT (현재 키워드 기준)")
    print("=" * 70)
    print(f"  뉴스 파일: {len(data.get('news_files', []))}개")
    print(f"  총 뉴스 (티커 있는): {data['total_news']}건")
    print()

    print("  [버킷 분포]")
    for b in ["POS_STRONG", "POS_WEAK", "NEG_STRONG", "NEG_WEAK", "IGNORE", "UNKNOWN"]:
        cnt = data["bucket_counts"].get(b, 0)
        if cnt:
            pct = cnt / data["total_news"] * 100
            print(f"    {b}: {cnt} ({pct:.1f}%)")
    print()

    print(f"  [POS_STRONG 샘플] ({data['pos_strong_count']}건 중 상위 10)")
    for h in data["pos_strong_sample"][:10]:
        tickers = ",".join(h["tickers"][:2])
        print(f"    [{h['date']}] {tickers} {h['title'][:40]} — {h['keyword_hits']}")
    print()

    print(f"  [POS_WEAK 샘플] ({data['pos_weak_count']}건 중 상위 10)")
    for h in data["pos_weak_sample"][:10]:
        tickers = ",".join(h["tickers"][:2])
        print(f"    [{h['date']}] {tickers} {h['title'][:40]} — {h['keyword_hits']}")
    print()

    print("  [뉴스 빈도 상위 티커]")
    for ticker, cnt in data["top_tickers"][:10]:
        print(f"    {ticker}: {cnt}건")

    print()
    print("=" * 70)


# ── CLI ──

def main() -> None:
    date_filter = ""
    reclassify_mode = "--reclassify" in sys.argv

    for i, arg in enumerate(sys.argv):
        if arg == "--date" and i + 1 < len(sys.argv):
            date_filter = sys.argv[i + 1]

    project_root = Path(__file__).resolve().parent.parent
    log_dir = project_root / "logs"
    snapshot_dir = project_root / "data" / "runtime" / "price_snapshots"
    news_dir = project_root / "data" / "collector" / "news"

    if reclassify_mode:
        data = reclassify_news(news_dir, date_filter)
        print_reclassify_report(data)
    else:
        data = replay_from_logs(log_dir, snapshot_dir, date_filter)
        print_replay_report(data)


if __name__ == "__main__":
    main()
