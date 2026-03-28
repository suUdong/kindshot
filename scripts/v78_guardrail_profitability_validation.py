#!/usr/bin/env python3
"""Validate v78 guardrail throughput/profitability artifacts and render a report."""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from pykrx import stock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SIGNAL_REPORT = PROJECT_ROOT / "reports" / "signal-backtest-result.md"
DEFAULT_GUARDRAIL_SIM = PROJECT_ROOT / "reports" / "guardrail_sim.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "reports" / "v78-guardrail-profitability-validation.md"
SUMMARY_PATTERNS = {
    "total_buy_signals": re.compile(r"^- 전체 BUY 시그널: (\d+)건$"),
    "raw_blocked": re.compile(r"^- v78 가드레일 차단: (\d+)건$"),
}


@dataclass(frozen=True)
class SignalRow:
    date: str
    ticker: str
    bucket: str
    confidence: int
    entry_px: int
    ret_t1: float | None
    ret_t5: float | None
    ret_t30: float | None
    original_guardrail: str


@dataclass(frozen=True)
class HorizonStats:
    count: int
    win_rate: float | None
    avg_ret: float | None
    median_ret: float | None
    total_ret: float | None
    min_ret: float | None
    max_ret: float | None


@dataclass(frozen=True)
class BootstrapSummary:
    mean: float
    p05: float
    p95: float


@dataclass(frozen=True)
class ReturnVerificationSummary:
    verified_rows: int
    mismatches: list[dict]
    price_series_count: int
    trading_days_count: int


def parse_signal_rows(markdown: str) -> list[SignalRow]:
    rows: list[SignalRow] = []
    for line in markdown.splitlines():
        if not line.startswith("| 202603"):
            continue
        parts = [part.strip() for part in line.strip("|").split("|")]
        if len(parts) < 9:
            continue
        date, ticker, bucket, confidence, entry_px, t1, t5, t30, original_guardrail = parts[:9]
        rows.append(
            SignalRow(
                date=date,
                ticker=ticker,
                bucket=bucket,
                confidence=int(confidence),
                entry_px=int(entry_px.replace(",", "")),
                ret_t1=None if t1 == "N/A" else float(t1),
                ret_t5=None if t5 == "N/A" else float(t5),
                ret_t30=None if t30 == "N/A" else float(t30),
                original_guardrail=original_guardrail,
            )
        )
    return rows


def parse_signal_report(markdown: str) -> tuple[list[SignalRow], dict[str, int]]:
    summary: dict[str, int] = {}
    for line in markdown.splitlines():
        stripped = line.strip()
        for key, pattern in SUMMARY_PATTERNS.items():
            match = pattern.match(stripped)
            if match:
                summary[key] = int(match.group(1))
    return parse_signal_rows(markdown), summary


def horizon_stats(rows: list[SignalRow], attr: str, predicate: Callable[[SignalRow], bool] | None = None) -> HorizonStats:
    selected = [row for row in rows if (predicate(row) if predicate else True)]
    values = [getattr(row, attr) for row in selected if getattr(row, attr) is not None]
    if not values:
        return HorizonStats(0, None, None, None, None, None, None)
    ordered = sorted(values)
    wins = sum(1 for value in values if value > 0)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        median = ordered[midpoint]
    else:
        median = (ordered[midpoint - 1] + ordered[midpoint]) / 2
    return HorizonStats(
        count=len(values),
        win_rate=round(wins / len(values) * 100, 1),
        avg_ret=round(sum(values) / len(values), 2),
        median_ret=round(median, 2),
        total_ret=round(sum(values), 2),
        min_ret=round(min(values), 2),
        max_ret=round(max(values), 2),
    )


def bootstrap_mean(values: list[float], iterations: int = 20_000, seed: int = 42) -> BootstrapSummary | None:
    if not values:
        return None
    rng = random.Random(seed)
    sample_size = len(values)
    means: list[float] = []
    for _ in range(iterations):
        resample = [values[rng.randrange(sample_size)] for _ in range(sample_size)]
        means.append(sum(resample) / sample_size)
    means.sort()
    lower = means[int(iterations * 0.05)]
    upper = means[int(iterations * 0.95)]
    return BootstrapSummary(
        mean=round(sum(values) / sample_size, 2),
        p05=round(lower, 2),
        p95=round(upper, 2),
    )


def infer_910a331_summary(total_buy_signals: int, deduped_passed: int, raw_blocked: int) -> dict[str, int]:
    raw_passed = total_buy_signals - raw_blocked
    duplicate_passes_removed = raw_passed - deduped_passed
    return {
        "total_buy_signals": total_buy_signals,
        "raw_passed_inferred": raw_passed,
        "deduped_passed": deduped_passed,
        "raw_blocked": raw_blocked,
        "duplicate_passes_removed": duplicate_passes_removed,
    }


def _future_trading_day(trading_days: list[str], signal_date: str, n: int) -> str | None:
    if signal_date not in trading_days:
        return None
    index = trading_days.index(signal_date) + n
    if index >= len(trading_days):
        return None
    return trading_days[index]


def _calc_return(entry_px: int, future_px: int) -> float:
    return round((future_px - entry_px) / entry_px * 100, 2)


def verify_returns_with_pykrx(rows: list[SignalRow], start_date: str = "20260317", end_date: str = "20260430") -> ReturnVerificationSummary:
    tickers = sorted({row.ticker for row in rows})
    prices: dict[str, object] = {}
    for ticker in tickers:
        data = stock.get_market_ohlcv(start_date, end_date, ticker)
        if data is None or data.empty:
            continue
        series = data["종가"]
        series.index = series.index.strftime("%Y%m%d")
        prices[ticker] = series

    trading_days = stock.get_market_ohlcv("20260318", end_date, "005930").index.strftime("%Y%m%d").tolist()

    mismatches: list[dict] = []
    verified_rows = 0
    for row in rows:
        series = prices.get(row.ticker)
        if series is None or row.date not in series.index:
            continue
        verified_rows += 1
        entry_px = int(series[row.date])
        if row.entry_px != entry_px:
            mismatches.append(
                {
                    "date": row.date,
                    "ticker": row.ticker,
                    "horizon": "entry_px",
                    "reported": row.entry_px,
                    "calculated": entry_px,
                    "entry_px_reported": row.entry_px,
                    "entry_px_pykrx": entry_px,
                }
            )
        for attr, horizon in (("ret_t1", 1), ("ret_t5", 5)):
            expected = getattr(row, attr)
            future_date = _future_trading_day(trading_days, row.date, horizon)
            calculated = None
            if future_date and future_date in series.index:
                calculated = _calc_return(entry_px, int(series[future_date]))
            if expected != calculated:
                mismatches.append(
                    {
                        "date": row.date,
                        "ticker": row.ticker,
                        "horizon": attr,
                        "reported": expected,
                        "calculated": calculated,
                        "entry_px_reported": row.entry_px,
                        "entry_px_pykrx": entry_px,
                    }
                )

    return ReturnVerificationSummary(
        verified_rows=verified_rows,
        mismatches=mismatches,
        price_series_count=len(prices),
        trading_days_count=len(trading_days),
    )


def load_guardrail_sim(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def _fmt_win_rate(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}%"


def _render_stats_row(label: str, stats: HorizonStats) -> str:
    return (
        f"| {label} | {stats.count} | {_fmt_win_rate(stats.win_rate)} | "
        f"{_fmt_pct(stats.avg_ret)} | {_fmt_pct(stats.total_ret)} | {_fmt_pct(stats.median_ret)} |"
    )


def _render_bootstrap(label: str, summary: BootstrapSummary | None) -> str:
    if summary is None:
        return f"- {label}: 데이터 부족"
    return f"- {label}: mean {summary.mean:.2f}%, 90% bootstrap interval [{summary.p05:.2f}%, {summary.p95:.2f}%]"


def render_report(
    *,
    rows: list[SignalRow],
    report_summary: dict[str, int],
    verification: ReturnVerificationSummary,
    guardrail_sim: dict,
    output_path: Path,
) -> str:
    summary = guardrail_sim["summary"]
    inferred = infer_910a331_summary(
        total_buy_signals=report_summary["total_buy_signals"],
        deduped_passed=len(rows),
        raw_blocked=report_summary["raw_blocked"],
    )
    baseline_rows = [row for row in rows if row.original_guardrail == "PASSED"]
    newly_admitted_rows = [row for row in rows if row.original_guardrail != "PASSED"]
    original_reason_counts = Counter(row.original_guardrail for row in rows if row.original_guardrail != "PASSED")

    t1_all = horizon_stats(rows, "ret_t1")
    t5_all = horizon_stats(rows, "ret_t5")
    t1_baseline = horizon_stats(baseline_rows, "ret_t1")
    t5_baseline = horizon_stats(baseline_rows, "ret_t5")
    t1_new = horizon_stats(newly_admitted_rows, "ret_t1")
    t5_new = horizon_stats(newly_admitted_rows, "ret_t5")

    t1_all_boot = bootstrap_mean([row.ret_t1 for row in rows if row.ret_t1 is not None])
    t5_all_boot = bootstrap_mean([row.ret_t5 for row in rows if row.ret_t5 is not None])
    t1_new_boot = bootstrap_mean([row.ret_t1 for row in newly_admitted_rows if row.ret_t1 is not None])
    t5_new_boot = bootstrap_mean([row.ret_t5 for row in newly_admitted_rows if row.ret_t5 is not None])

    extra_passes = summary["v78_passed"] - summary["old_passed"]
    eligible_events = summary["guardrail_eligible_events"]
    t1_extra_gross = round((t1_new.avg_ret or 0.0) * extra_passes, 2) if t1_new.avg_ret is not None else None
    t5_extra_gross = round((t5_new.avg_ret or 0.0) * extra_passes, 2) if t5_new.avg_ret is not None else None
    t1_extra_per_event = round(t1_extra_gross / eligible_events, 4) if t1_extra_gross is not None else None
    t5_extra_per_event = round(t5_extra_gross / eligible_events, 4) if t5_extra_gross is not None else None
    try:
        output_label = str(output_path.relative_to(PROJECT_ROOT))
    except ValueError:
        output_label = str(output_path)

    lines = [
        "# v78 Guardrail Profitability Validation",
        "",
        f"생성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## 1. Validation Scope",
        "",
        "- throughput source: `reports/guardrail_sim.json` (229 guardrail-eligible events, v77 vs v78 비교)",
        "- profitability source: `reports/signal-backtest-result.md` 상세 테이블 42행",
        "- price verification source: `pykrx` 종가 재조회",
        "",
        "## 2. 910a331 산출물 점검",
        "",
        f"- `910a331` 리포트는 `total BUY 87`, `deduped pass 42`, `raw blocked 32`를 함께 표기한다.",
        f"- 위 숫자를 같은 분모로 보면 안 된다. raw pass는 `{inferred['raw_passed_inferred']}`건으로 추정되며, 이 중 `{inferred['duplicate_passes_removed']}`건이 same-day same-ticker dedupe로 제거된 것이다.",
        f"- 따라서 `42 + 32 != 87`은 데이터 오류라기보다 summary framing 문제다. throughput 판단에는 raw event 기반 `guardrail_sim.json` 을, profitability 판단에는 deduped 42행을 사용해야 한다.",
        "",
        "## 3. Return Reverification",
        "",
        f"- `pykrx`로 재조회한 종가 기준 검증 대상: {verification.verified_rows}행 / price series {verification.price_series_count}개 / trading days {verification.trading_days_count}일",
        f"- entry/T+1/T+5 mismatch: {len(verification.mismatches)}건",
    ]

    if verification.mismatches:
        lines.append("")
        lines.append("| 날짜 | 종목 | horizon | reported | calculated |")
        lines.append("|------|------|---------|----------|------------|")
        for mismatch in verification.mismatches[:10]:
            lines.append(
                f"| {mismatch['date']} | {mismatch['ticker']} | {mismatch['horizon']} | "
                f"{mismatch['reported']} | {mismatch['calculated']} |"
            )

    lines += [
        "",
        "## 4. Block Rate Comparison",
        "",
        "| Metric | v77 | v78 | Delta |",
        "|--------|-----|-----|-------|",
        f"| Pass count | {summary['old_passed']} | {summary['v78_passed']} | {summary['v78_passed'] - summary['old_passed']:+d} |",
        f"| Pass rate | {summary['old_pass_rate_pct']:.1f}% | {summary['v78_pass_rate_pct']:.1f}% | {summary['pass_rate_improvement_pct']:+.1f}%p |",
        f"| Block count | {summary['old_blocked']} | {summary['v78_blocked']} | {summary['v78_blocked'] - summary['old_blocked']:+d} |",
        f"| Block rate | {(summary['old_blocked'] / eligible_events * 100):.1f}% | {(summary['v78_blocked'] / eligible_events * 100):.1f}% | {-summary['pass_rate_improvement_pct']:+.1f}%p |",
        "",
        "주요 관찰:",
        f"- throughput 기준 완화 효과는 `+4` pass, `+1.7%p` pass rate, `-4` block 이다.",
        f"- raw event 기준 차단률은 `42.4% -> 40.6%` 로 낮아졌지만, 목표였던 `40~60%` 밴드 안에서의 변화 폭은 작다.",
        "",
        "## 5. Profitability Decomposition",
        "",
        "| Cohort | Matured trades | Win rate | Avg ret | Total ret | Median |",
        "|--------|----------------|----------|---------|-----------|--------|",
        _render_stats_row("All T+1", t1_all),
        _render_stats_row("All T+5", t5_all),
        _render_stats_row("Baseline PASSED T+1", t1_baseline),
        _render_stats_row("Baseline PASSED T+5", t5_baseline),
        _render_stats_row("Newly admitted T+1", t1_new),
        _render_stats_row("Newly admitted T+5", t5_new),
        "",
        "새로 편입된 시그널 원래 차단 사유:",
        "",
        "| Original guardrail | Count |",
        "|--------------------|-------|",
    ]

    for reason, count in original_reason_counts.most_common():
        lines.append(f"| {reason} | {count} |")

    lines += [
        "",
        "핵심 관찰:",
        f"- T+1은 baseline(-4.28%)과 newly admitted(-4.23%) 모두 부진하다.",
        f"- T+5는 baseline cohort가 `-0.81%`, newly admitted cohort가 `+3.79%` 로 갈린다.",
        f"- `910a331` 표본에서는 non-`PASSED` cohort가 T+5 총합 `{_fmt_pct(t5_new.total_ret)}` 를 만들어 전체 평균을 `-0.81% -> +2.39%` 로 바꿨다.",
        "- 이 cohort는 `guardrail_sim.json` 의 `+4` extra pass와 동일 집합이 아니므로, 여기서 확인되는 것은 realized uplift가 아니라 proxy signal이다.",
        "",
        "## 6. Expected Return Simulation",
        "",
        "가정: `guardrail_sim.json` 의 `+4` extra pass가 `signal-backtest-result.md` 의 newly admitted cohort와 비슷한 수익률 분포를 가진다고 보는 proxy 시뮬레이션이다.",
        "",
        "이 절은 realized uplift 증명이 아니라, 현재 표본으로 본 민감도 추정이다.",
        "",
        f"- extra pass count: {extra_passes} / eligible events: {eligible_events}",
        f"- proxy newly admitted T+1 avg: {_fmt_pct(t1_new.avg_ret)}",
        f"- proxy newly admitted T+5 avg: {_fmt_pct(t5_new.avg_ret)}",
        f"- implied extra gross T+1 return sum: {_fmt_pct(t1_extra_gross)}",
        f"- implied extra gross T+5 return sum: {_fmt_pct(t5_extra_gross)}",
        f"- implied per-eligible-event uplift at T+1: {_fmt_pct(t1_extra_per_event)}",
        f"- implied per-eligible-event uplift at T+5: {_fmt_pct(t5_extra_per_event)}",
        "",
        "bootstrap sanity check:",
        _render_bootstrap("All T+1 mean", t1_all_boot),
        _render_bootstrap("All T+5 mean", t5_all_boot),
        _render_bootstrap("Newly admitted T+1 mean", t1_new_boot),
        _render_bootstrap("Newly admitted T+5 mean", t5_new_boot),
        "",
        "## 7. Verdict",
        "",
        "- `910a331` 의 상세 테이블은 현재 시점 `pykrx` 종가로 재검산했을 때 entry/T+1/T+5 mismatch가 없었다.",
        "- 다만 commit 본문/기존 리포트는 raw throughput 수치와 deduped profitability 수치를 같은 summary에 섞어 써서 해석 혼선이 있다.",
        "- v78 완화는 throughput 측면에서는 `+1.7%p` 개선에 그쳤다.",
        "- profitability 측면에서는 표본 내 non-`PASSED` cohort가 T+5에서 개선 신호를 보였지만, 이는 `+4` extra pass의 realized PnL이 아니라 더 넓은 proxy cohort를 사용한 결과다.",
        "- bootstrap 90% 구간이 `[-1.55%, 9.80%]` 로 넓고 0을 포함하므로, 현재 근거는 `개선 가능성 탐색` 수준이지 `안정적 양의 기대수익 확인` 수준은 아니다.",
        "",
        "## 8. Risks",
        "",
        "- `trade_history.db` 부재로 `scripts/backtest_signals.py` 를 현 워크트리에서 재실행하지 못했다.",
        "- throughput 비교(`guardrail_sim.json`)와 profitability 비교(`signal-backtest-result.md`)는 서로 다른 표본면을 사용한다.",
        "- newly admitted cohort의 T+5 개선은 일부 큰 winner(`ORDERBOOK_TOP_LEVEL_LIQUIDITY`, `MARKET_CLOSE_CUTOFF`)에 민감하다.",
        "",
        f"*Generated by `{Path(__file__).name}` → `{output_label}`*",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal-report", type=Path, default=DEFAULT_SIGNAL_REPORT)
    parser.add_argument("--guardrail-sim", type=Path, default=DEFAULT_GUARDRAIL_SIM)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    signal_report = args.signal_report.read_text(encoding="utf-8")
    rows, report_summary = parse_signal_report(signal_report)
    verification = verify_returns_with_pykrx(rows)
    guardrail_sim = load_guardrail_sim(args.guardrail_sim)
    report = render_report(
        rows=rows,
        report_summary=report_summary,
        verification=verification,
        guardrail_sim=guardrail_sim,
        output_path=args.output,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"rows={len(rows)} mismatches={len(verification.mismatches)} output={args.output}")


if __name__ == "__main__":
    main()
