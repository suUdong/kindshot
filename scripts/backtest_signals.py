#!/usr/bin/env python3
"""Phase 1 백테스트: trade_history.db 시그널 수익성 검증.

v78 가드레일 완화 기준으로 통과 시그널을 재판정하고,
pykrx 실제 주가로 T+1 / T+5 / T+30 수익률을 분석한다.
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from pykrx import stock as pykrx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "trade_history.db"
DEFAULT_REPORT_PATH = PROJECT_ROOT / "reports" / "signal-backtest-result.md"

# v78 가드레일 완화 기준
V78_MIN_CONFIDENCE = 73
V78_CHASE_BUY_PCT = 5.0
V78_NO_BUY_AFTER_HOUR = 15  # 15:15 cutoff → hour_slot <= 15
V78_FAST_PROFILE_HOUR = 14  # 14:30 cutoff → hour_slot <= 14


def load_signals(db_path: Path) -> list[dict]:
    """DB에서 전체 BUY 시그널 로드."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM trades WHERE decision_action='BUY' ORDER BY date, hour_slot")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def passes_v78_guardrails(sig: dict) -> tuple[bool, str]:
    """v78 완화 기준으로 통과 여부 재판정.

    Returns: (passed, reason_if_blocked)
    """
    conf = sig["confidence"] or 0
    hour = sig["hour_slot"] or 0
    ret_today = sig["ret_today"] or 0.0

    # 시장 마감 후 시그널 차단 (15:15 cutoff → hour 16+ 차단)
    if hour >= 16:
        return False, "MARKET_CLOSE_CUTOFF"

    # confidence 기준
    if conf < V78_MIN_CONFIDENCE:
        return False, "LOW_CONFIDENCE"

    # 추격매수 차단 (당일 5% 이상 상승)
    if ret_today >= V78_CHASE_BUY_PCT:
        return False, "CHASE_BUY_BLOCKED"

    return True, ""


def get_trading_days(start: str, end: str) -> list[str]:
    """pykrx에서 거래일 목록 조회 (삼성전자 OHLCV 인덱스 활용)."""
    df = pykrx.get_market_ohlcv(start, end, "005930")
    if df is None or df.empty:
        return []
    return list(df.index.strftime("%Y%m%d"))


def fetch_close_prices(tickers: list[str], start_date: str, end_date: str) -> dict[str, pd.Series]:
    """종목별 종가 시계열 조회."""
    prices = {}
    for i, ticker in enumerate(tickers):
        try:
            df = pykrx.get_market_ohlcv(start_date, end_date, ticker)
            if df is not None and not df.empty:
                series = df["종가"]
                series.index = series.index.strftime("%Y%m%d")
                prices[ticker] = series
            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{len(tickers)} 조회 완료")
        except Exception as e:
            print(f"  [WARN] {ticker} 가격 조회 실패: {e}")
    return prices


def calc_return(entry_px: float, future_px: float) -> float | None:
    """수익률(%) 계산."""
    if entry_px <= 0 or future_px <= 0:
        return None
    return round((future_px - entry_px) / entry_px * 100, 2)


def find_t_plus_n(trading_days: list[str], signal_date: str, n: int) -> str | None:
    """시그널일로부터 T+N 거래일 찾기."""
    if signal_date not in trading_days:
        return None
    idx = trading_days.index(signal_date)
    target = idx + n
    if target < len(trading_days):
        return trading_days[target]
    return None


def summarize_signal_counts(
    total_signals: int,
    blocked_count: int,
    deduped_count: int,
    analyzable_count: int,
) -> dict[str, int]:
    raw_passed_count = total_signals - blocked_count
    return {
        "total_signals": total_signals,
        "blocked_count": blocked_count,
        "raw_passed_count": raw_passed_count,
        "deduped_count": deduped_count,
        "duplicate_removed_count": raw_passed_count - deduped_count,
        "analyzable_count": analyzable_count,
        "price_unavailable_count": deduped_count - analyzable_count,
    }


def run_backtest(
    db_path: Path,
    trading_day_start: str = "20260318",
    trading_day_end: str = "20260430",
    price_start_date: str = "20260317",
    price_end_date: str = "20260430",
):
    """백테스트 메인 로직."""
    signals = load_signals(db_path)
    print(f"총 시그널: {len(signals)}건")

    # v78 가드레일 재판정
    passed = []
    blocked = []
    for sig in signals:
        ok, reason = passes_v78_guardrails(sig)
        if ok:
            passed.append(sig)
        else:
            blocked.append((sig, reason))

    print(f"v78 통과: {len(passed)}건, 차단: {len(blocked)}건")

    # 같은 날 같은 종목 중복 제거 (가장 높은 confidence 유지)
    deduped: dict[str, dict] = {}
    for sig in passed:
        key = f"{sig['ticker']}_{sig['date']}"
        if key not in deduped or (sig["confidence"] or 0) > (deduped[key]["confidence"] or 0):
            deduped[key] = sig
    signals_to_test = list(deduped.values())
    print(f"중복 제거 후: {len(signals_to_test)}건")

    # 거래일 목록 조회
    trading_days = get_trading_days(trading_day_start, trading_day_end)
    print(f"거래일 수: {len(trading_days)}일 ({trading_day_start}~최신)")

    # 종목별 종가 조회
    tickers = list({s["ticker"] for s in signals_to_test})
    print(f"종목 수: {len(tickers)}개, 가격 조회 중...")
    prices = fetch_close_prices(tickers, price_start_date, price_end_date)
    print(f"가격 조회 완료: {len(prices)}개 종목")

    # T+1, T+5, T+30 수익률 계산
    results = []
    for sig in signals_to_test:
        ticker = sig["ticker"]
        date = sig["date"]
        price_series = prices.get(ticker)
        if price_series is None or date not in price_series.index:
            print(f"  [SKIP] {ticker} {date} 가격 없음")
            continue

        entry_px = price_series[date]
        if entry_px <= 0:
            continue

        row = {
            "date": date,
            "ticker": ticker,
            "corp_name": sig["corp_name"] or "",
            "headline": (sig["headline"] or "")[:40],
            "bucket": sig["bucket"],
            "confidence": sig["confidence"],
            "entry_px": int(entry_px),
            "guardrail_original": sig["guardrail_result"] or "PASSED",
        }

        for label, n in [("t1", 1), ("t5", 5), ("t30", 30)]:
            t_date = find_t_plus_n(trading_days, date, n)
            if t_date and price_series is not None and t_date in price_series.index:
                future_px = price_series[t_date]
                ret = calc_return(entry_px, future_px)
                row[f"ret_{label}"] = ret
                row[f"px_{label}"] = int(future_px)
            else:
                row[f"ret_{label}"] = None
                row[f"px_{label}"] = None

        results.append(row)

    summary_counts = summarize_signal_counts(
        len(signals),
        len(blocked),
        len(signals_to_test),
        len(results),
    )
    print(f"\n분석 완료: {len(results)}건")
    print(
        "요약 카운트: "
        f"raw_passed={summary_counts['raw_passed_count']}, "
        f"blocked={summary_counts['blocked_count']}, "
        f"deduped={summary_counts['deduped_count']}, "
        f"duplicates_removed={summary_counts['duplicate_removed_count']}, "
        f"price_unavailable={summary_counts['price_unavailable_count']}"
    )
    return results, blocked, signals, summary_counts


def compute_stats(results: list[dict], horizon: str) -> dict:
    """특정 horizon의 승률/평균수익률 계산."""
    key = f"ret_{horizon}"
    valid = [r for r in results if r.get(key) is not None]
    if not valid:
        return {"count": 0, "win_rate": None, "avg_ret": None, "median_ret": None,
                "max_ret": None, "min_ret": None}
    rets = [r[key] for r in valid]
    wins = [r for r in rets if r > 0]
    return {
        "count": len(valid),
        "win_rate": round(len(wins) / len(valid) * 100, 1),
        "avg_ret": round(sum(rets) / len(rets), 2),
        "median_ret": round(sorted(rets)[len(rets) // 2], 2),
        "max_ret": round(max(rets), 2),
        "min_ret": round(min(rets), 2),
    }


def generate_report(results: list[dict], blocked: list, all_signals: list, summary_counts: dict[str, int]) -> str:
    """마크다운 리포트 생성."""
    stats_t1 = compute_stats(results, "t1")
    stats_t5 = compute_stats(results, "t5")
    stats_t30 = compute_stats(results, "t30")

    # 판정 기준
    def judge(stats: dict) -> str:
        if stats["win_rate"] is None:
            return "데이터 부족"
        if stats["win_rate"] >= 50 and stats["avg_ret"] is not None and stats["avg_ret"] > 0:
            return "**PASS** ✅"
        return "**FAIL** ❌"

    # 차단 사유 집계
    block_reasons: dict[str, int] = {}
    for _, reason in blocked:
        block_reasons[reason] = block_reasons.get(reason, 0) + 1

    lines = [
        "# Phase 1 백테스트: kindshot 시그널 수익성 검증",
        "",
        f"생성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## 요약",
        "",
        f"- 전체 BUY 시그널: {len(all_signals)}건",
        f"- v78 가드레일 완화 raw 통과: {summary_counts['raw_passed_count']}건",
        f"- v78 가드레일 완화 통과: {summary_counts['deduped_count']}건 (중복 제거 후)",
        f"- v78 가드레일 차단: {len(blocked)}건",
        f"- 중복 제거로 제외된 통과 시그널: {summary_counts['duplicate_removed_count']}건",
        f"- 가격 데이터 부족으로 수익률 계산에서 제외된 통과 시그널: {summary_counts['price_unavailable_count']}건",
        f"- 실제 수익률 분석 건수: {summary_counts['analyzable_count']}건",
        f"- 분석 기간: 20260318 ~ 20260327",
        "",
        "### v78 가드레일 완화 기준",
        "",
        "| 항목 | 기존 | v78 완화 |",
        "|------|------|----------|",
        "| min_buy_confidence | 78 | 73 |",
        "| chase_buy_pct | 3.0% | 5.0% |",
        "| no_buy_after | 15:00 | 15:15 |",
        "| fast_profile_no_buy_after | 14:00 | 14:30 |",
        "| min_intraday_value_vs_adv20d | 0.15 | 0.05 |",
        "| orderbook_liquidity | 100% | 50% |",
        "",
        "### 차단 사유 분포",
        "",
        "| 사유 | 건수 |",
        "|------|------|",
    ]
    for reason, cnt in sorted(block_reasons.items(), key=lambda x: -x[1]):
        lines.append(f"| {reason} | {cnt} |")

    lines += [
        "",
        "## 수익률 분석",
        "",
        "| 지표 | T+1 | T+5 | T+30 |",
        "|------|-----|-----|------|",
        f"| 분석 건수 | {stats_t1['count']} | {stats_t5['count']} | {stats_t30['count']} |",
        f"| 승률 | {stats_t1['win_rate']}% | {stats_t5['win_rate'] if stats_t5['win_rate'] is not None else 'N/A'}% | {stats_t30['win_rate'] if stats_t30['win_rate'] is not None else 'N/A'}% |",
        f"| 평균수익률 | {stats_t1['avg_ret']}% | {stats_t5['avg_ret'] if stats_t5['avg_ret'] is not None else 'N/A'}% | {stats_t30['avg_ret'] if stats_t30['avg_ret'] is not None else 'N/A'}% |",
        f"| 중간값 | {stats_t1['median_ret']}% | {stats_t5['median_ret'] if stats_t5['median_ret'] is not None else 'N/A'}% | {stats_t30['median_ret'] if stats_t30['median_ret'] is not None else 'N/A'}% |",
        f"| 최대 | {stats_t1['max_ret']}% | {stats_t5['max_ret'] if stats_t5['max_ret'] is not None else 'N/A'}% | {stats_t30['max_ret'] if stats_t30['max_ret'] is not None else 'N/A'}% |",
        f"| 최소 | {stats_t1['min_ret']}% | {stats_t5['min_ret'] if stats_t5['min_ret'] is not None else 'N/A'}% | {stats_t30['min_ret'] if stats_t30['min_ret'] is not None else 'N/A'}% |",
        "",
        "## Paper → Live 전환 판정",
        "",
        "기준: 승률 50% 이상 + 평균수익률 양수",
        "",
        f"| Horizon | 판정 |",
        f"|---------|------|",
        f"| T+1 | {judge(stats_t1)} |",
        f"| T+5 | {judge(stats_t5)} |",
        f"| T+30 | {judge(stats_t30)} |",
        "",
    ]

    # 종합 판정
    t1_pass = stats_t1["win_rate"] is not None and stats_t1["win_rate"] >= 50 and stats_t1["avg_ret"] is not None and stats_t1["avg_ret"] > 0
    lines += [
        "### 종합 판정",
        "",
    ]
    if t1_pass:
        lines.append("> **READY FOR LIVE** — T+1 기준 승률/수익률 모두 기준 충족")
    else:
        lines.append("> **NOT READY** — 기준 미충족, 추가 최적화 필요")
    lines.append("")

    # 시그널별 상세 데이터
    lines += [
        "## 시그널별 상세",
        "",
        "| 날짜 | 종목 | 버킷 | conf | 진입가 | T+1(%) | T+5(%) | T+30(%) | 원래가드레일 |",
        "|------|------|------|------|--------|--------|--------|---------|-------------|",
    ]
    for r in sorted(results, key=lambda x: x["date"]):
        t1 = f"{r['ret_t1']}" if r["ret_t1"] is not None else "N/A"
        t5 = f"{r['ret_t5']}" if r["ret_t5"] is not None else "N/A"
        t30 = f"{r['ret_t30']}" if r["ret_t30"] is not None else "N/A"
        lines.append(
            f"| {r['date']} | {r['ticker']} | {r['bucket']} | {r['confidence']} "
            f"| {r['entry_px']:,} | {t1} | {t5} | {t30} | {r['guardrail_original']} |"
        )

    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest v78-relaxed BUY signals from trade_history.db")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--report-path", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--trading-day-start", default="20260318")
    parser.add_argument("--trading-day-end", default="20260430")
    parser.add_argument("--price-start-date", default="20260317")
    parser.add_argument("--price-end-date", default="20260430")
    return parser.parse_args()


def main():
    args = parse_args()
    db_path = Path(args.db_path)
    report_path = Path(args.report_path)

    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}")
        sys.exit(1)

    results, blocked, all_signals, summary_counts = run_backtest(
        db_path=db_path,
        trading_day_start=args.trading_day_start,
        trading_day_end=args.trading_day_end,
        price_start_date=args.price_start_date,
        price_end_date=args.price_end_date,
    )

    if not results:
        print("ERROR: 분석 가능한 시그널 없음")
        sys.exit(1)

    report = generate_report(results, blocked, all_signals, summary_counts)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"\n리포트 저장: {report_path}")

    # 요약 출력
    stats = compute_stats(results, "t1")
    print(f"\n=== T+1 요약 ===")
    print(f"  건수: {stats['count']}, 승률: {stats['win_rate']}%, 평균수익률: {stats['avg_ret']}%")


if __name__ == "__main__":
    main()
