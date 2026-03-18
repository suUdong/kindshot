#!/usr/bin/env python3
"""
strategy-improve.py — Data-driven strategy improvement loop for Kindshot.

Analyzes trading performance, identifies improvement opportunities,
implements changes, backtests via replay, and deploys if profitable.

Usage:
    python scripts/strategy-improve.py analyze       # Analyze recent performance
    python scripts/strategy-improve.py propose        # Propose next improvement
    python scripts/strategy-improve.py run            # Full cycle: analyze → propose → implement → backtest
    python scripts/strategy-improve.py run --loop 3   # Run up to 3 improvement cycles
"""

import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_DIR / "logs"
DATA_DIR = PROJECT_DIR / "data"
REPLAY_DIR = DATA_DIR / "replay" / "day_reports"
RUNTIME_DIR = DATA_DIR / "runtime"
MEMORY_DIR = PROJECT_DIR / "memory" / "strategy-loop"
HYPOTHESIS_FILE = MEMORY_DIR / "hypotheses.jsonl"
PERFORMANCE_FILE = MEMORY_DIR / "performance.json"

OPENDEV_CMD = os.environ.get("OPENDEV_CMD", "opendev")


@dataclass
class TradeOutcome:
    """Single trade result from logs."""
    date: str
    ticker: str
    bucket: str
    headline: str
    confidence: int
    size_hint: str
    ret_1m: float | None = None
    ret_5m: float | None = None
    ret_30m: float | None = None
    ret_close: float | None = None


@dataclass
class SkippedSignal:
    """Signal that was skipped and what happened after."""
    date: str
    ticker: str
    bucket: str
    headline: str
    skip_stage: str
    skip_reason: str
    ret_close: float | None = None  # what would've happened


@dataclass
class PerformanceAnalysis:
    """Aggregated performance metrics."""
    total_events: int = 0
    total_trades: int = 0
    total_skips: int = 0
    bucket_stats: dict = field(default_factory=dict)
    skip_reasons: dict = field(default_factory=dict)
    profitable_trades: int = 0
    losing_trades: int = 0
    avg_ret_close: float = 0.0
    best_trade: TradeOutcome | None = None
    worst_trade: TradeOutcome | None = None
    missed_opportunities: list = field(default_factory=list)
    insights: list = field(default_factory=list)


def parse_log_file(path: Path) -> tuple[list[TradeOutcome], list[SkippedSignal]]:
    """Parse a kindshot JSONL log file for trades and skips."""
    trades = []
    skips = []
    events_by_id = {}
    decisions_by_id = {}
    snapshots_by_ticker_date = {}

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            rtype = record.get("type", "")

            if rtype == "event":
                eid = record.get("event_id", "")
                events_by_id[eid] = record

            elif rtype == "decision":
                eid = record.get("event_id", "")
                decisions_by_id[eid] = record

            elif rtype == "price_snapshot":
                ticker = record.get("ticker", "")
                date = record.get("date", "")
                label = record.get("label", "")
                key = f"{ticker}_{date}"
                if key not in snapshots_by_ticker_date:
                    snapshots_by_ticker_date[key] = {}
                snapshots_by_ticker_date[key][label] = record

    # Reconstruct trades and skips
    date_str = path.stem.replace("kindshot_", "")

    for eid, event in events_by_id.items():
        ticker = event.get("ticker", "")
        bucket = event.get("bucket", "UNKNOWN")
        headline = event.get("headline", "")
        skip_stage = event.get("skip_stage")
        skip_reason = event.get("skip_reason", "")

        snap_key = f"{ticker}_{date_str}"
        snaps = snapshots_by_ticker_date.get(snap_key, {})

        if eid in decisions_by_id:
            dec = decisions_by_id[eid]
            action = dec.get("action", "SKIP")
            if action == "BUY":
                trade = TradeOutcome(
                    date=date_str,
                    ticker=ticker,
                    bucket=bucket,
                    headline=headline,
                    confidence=dec.get("confidence", 0),
                    size_hint=dec.get("size_hint", "S"),
                )
                # Extract returns from snapshots
                if "t0" in snaps and "close" in snaps:
                    t0_price = snaps["t0"].get("price", 0)
                    if t0_price > 0:
                        for label, attr in [
                            ("t+1m", "ret_1m"),
                            ("t+5m", "ret_5m"),
                            ("t+30m", "ret_30m"),
                            ("close", "ret_close"),
                        ]:
                            if label in snaps:
                                snap_price = snaps[label].get("price", 0)
                                if snap_price > 0:
                                    setattr(
                                        trade,
                                        attr,
                                        (snap_price - t0_price) / t0_price * 100,
                                    )
                trades.append(trade)
        elif skip_stage:
            skip = SkippedSignal(
                date=date_str,
                ticker=ticker,
                bucket=bucket,
                headline=headline,
                skip_stage=skip_stage,
                skip_reason=skip_reason,
            )
            skips.append(skip)

    return trades, skips


def analyze_performance(days: int = 14) -> PerformanceAnalysis:
    """Analyze recent trading performance from logs."""
    analysis = PerformanceAnalysis()

    # Find recent log files
    log_files = sorted(LOGS_DIR.glob("kindshot_*.jsonl"), reverse=True)[:days]

    if not log_files:
        analysis.insights.append("No log files found. Cannot analyze performance.")
        return analysis

    all_trades: list[TradeOutcome] = []
    all_skips: list[SkippedSignal] = []

    for lf in log_files:
        trades, skips = parse_log_file(lf)
        all_trades.extend(trades)
        all_skips.extend(skips)

    analysis.total_events = len(all_trades) + len(all_skips)
    analysis.total_trades = len(all_trades)
    analysis.total_skips = len(all_skips)

    # Bucket distribution
    bucket_counter = Counter()
    for t in all_trades:
        bucket_counter[t.bucket] += 1
    for s in all_skips:
        bucket_counter[s.bucket] += 1
    analysis.bucket_stats = dict(bucket_counter.most_common())

    # Skip reason distribution
    skip_counter = Counter(s.skip_reason for s in all_skips)
    analysis.skip_reasons = dict(skip_counter.most_common(15))

    # Trade performance
    trades_with_close = [t for t in all_trades if t.ret_close is not None]
    if trades_with_close:
        analysis.profitable_trades = sum(1 for t in trades_with_close if t.ret_close > 0)
        analysis.losing_trades = sum(1 for t in trades_with_close if t.ret_close <= 0)
        analysis.avg_ret_close = sum(t.ret_close for t in trades_with_close) / len(
            trades_with_close
        )
        analysis.best_trade = max(trades_with_close, key=lambda t: t.ret_close)
        analysis.worst_trade = min(trades_with_close, key=lambda t: t.ret_close)

    # Missed opportunities: POS_STRONG signals that were skipped
    missed = [
        s
        for s in all_skips
        if s.bucket in ("POS_STRONG", "POS_WEAK") and s.skip_stage in ("QUANT", "GUARDRAIL")
    ]
    analysis.missed_opportunities = missed[:10]

    # Generate insights
    if analysis.total_trades == 0:
        analysis.insights.append(
            f"NO TRADES executed in {len(log_files)} days. "
            f"{analysis.total_skips} signals all skipped."
        )
        if all_skips:
            top_skip = skip_counter.most_common(1)[0]
            analysis.insights.append(
                f"Top skip reason: '{top_skip[0]}' ({top_skip[1]} times). "
                "Consider relaxing filters."
            )

    if analysis.total_trades > 0:
        win_rate = analysis.profitable_trades / analysis.total_trades * 100
        analysis.insights.append(
            f"Win rate: {win_rate:.0f}% ({analysis.profitable_trades}/{analysis.total_trades})"
        )
        analysis.insights.append(f"Avg return at close: {analysis.avg_ret_close:.2f}%")

    # Check bucket leakage
    unknown_count = bucket_counter.get("UNKNOWN", 0)
    if unknown_count > 0 and analysis.total_events > 0:
        unknown_pct = unknown_count / analysis.total_events * 100
        if unknown_pct > 20:
            analysis.insights.append(
                f"UNKNOWN bucket is {unknown_pct:.0f}% of events. "
                "Bucket keywords need expansion."
            )

    # Check if quant filter is too aggressive
    quant_skips = sum(1 for s in all_skips if s.skip_stage == "QUANT")
    if quant_skips > 0 and analysis.total_events > 0:
        quant_pct = quant_skips / analysis.total_events * 100
        if quant_pct > 60:
            analysis.insights.append(
                f"Quant pre-filter skips {quant_pct:.0f}% of events. "
                "ADV/spread thresholds may be too strict."
            )

    # Check for specific bucket performance
    bucket_trade_rets = defaultdict(list)
    for t in trades_with_close:
        bucket_trade_rets[t.bucket].append(t.ret_close)
    for bucket, rets in bucket_trade_rets.items():
        avg = sum(rets) / len(rets)
        if avg < -1:
            analysis.insights.append(
                f"Bucket '{bucket}' has avg return {avg:.2f}%. "
                "LLM prompt may need refinement for this category."
            )

    # Replay report analysis
    replay_reports = sorted(REPLAY_DIR.glob("*.json"), reverse=True)[:days]
    llm_errors = 0
    for rr in replay_reports:
        try:
            report = json.loads(rr.read_text())
            llm_errors += report.get("summary", {}).get("llm_errors", 0)
        except (json.JSONDecodeError, OSError):
            pass
    if llm_errors > 0:
        analysis.insights.append(
            f"{llm_errors} LLM errors in replay reports. "
            "API retry logic or context card robustness needs work."
        )

    return analysis


def format_analysis(analysis: PerformanceAnalysis) -> str:
    """Format analysis into a readable report."""
    lines = [
        "# Strategy Performance Analysis",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"## Summary",
        f"- Total events: {analysis.total_events}",
        f"- Trades executed: {analysis.total_trades}",
        f"- Signals skipped: {analysis.total_skips}",
        f"- Profitable: {analysis.profitable_trades}",
        f"- Losing: {analysis.losing_trades}",
        f"- Avg return (close): {analysis.avg_ret_close:.2f}%",
        "",
    ]

    if analysis.best_trade:
        t = analysis.best_trade
        lines.append(f"## Best Trade")
        lines.append(f"- {t.ticker} ({t.bucket}): +{t.ret_close:.2f}% | {t.headline[:60]}")
        lines.append("")

    if analysis.worst_trade:
        t = analysis.worst_trade
        lines.append(f"## Worst Trade")
        lines.append(f"- {t.ticker} ({t.bucket}): {t.ret_close:.2f}% | {t.headline[:60]}")
        lines.append("")

    lines.append("## Bucket Distribution")
    for bucket, count in analysis.bucket_stats.items():
        lines.append(f"- {bucket}: {count}")
    lines.append("")

    lines.append("## Top Skip Reasons")
    for reason, count in list(analysis.skip_reasons.items())[:10]:
        lines.append(f"- {reason}: {count}")
    lines.append("")

    if analysis.missed_opportunities:
        lines.append("## Potential Missed Opportunities (POS skipped by QUANT/GUARDRAIL)")
        for m in analysis.missed_opportunities[:5]:
            lines.append(f"- {m.ticker} ({m.skip_reason}): {m.headline[:60]}")
        lines.append("")

    lines.append("## Insights")
    for insight in analysis.insights:
        lines.append(f"- {insight}")

    return "\n".join(lines)


@dataclass
class Hypothesis:
    """A proposed strategy improvement."""
    id: str
    category: str  # bucket_keywords | quant_threshold | llm_prompt | guardrail | position_sizing
    title: str
    rationale: str
    target_file: str
    change_description: str
    expected_impact: str
    backtest_metric: str  # what to measure


HYPOTHESIS_CATEGORIES = [
    {
        "category": "bucket_keywords",
        "description": "Improve bucket classification (bucket.py) to catch missed signals or filter noise",
        "target": "src/kindshot/bucket.py",
        "when": "UNKNOWN bucket > 20% or missed POS signals found",
    },
    {
        "category": "quant_threshold",
        "description": "Tune quant pre-filter thresholds (ADV, spread, extreme move) in config.py",
        "target": "src/kindshot/config.py",
        "when": "Quant filter skips > 60% or profitable signals filtered out",
    },
    {
        "category": "llm_prompt",
        "description": "Improve LLM decision prompt in decision.py for better signal quality",
        "target": "src/kindshot/decision.py",
        "when": "Low win rate or poor avg returns on executed trades",
    },
    {
        "category": "guardrail",
        "description": "Adjust guardrail parameters (max loss, position limits, sector concentration)",
        "target": "src/kindshot/guardrails.py",
        "when": "Guardrail skipping profitable trades or not catching losers",
    },
    {
        "category": "context_enrichment",
        "description": "Improve context card data quality for better LLM decisions",
        "target": "src/kindshot/context_card.py",
        "when": "LLM errors or decisions with incomplete context",
    },
]


def propose_hypothesis(analysis: PerformanceAnalysis) -> Hypothesis | None:
    """Propose the highest-impact improvement based on performance analysis."""

    # Load previous hypotheses to avoid repeats
    tried = set()
    if HYPOTHESIS_FILE.exists():
        for line in HYPOTHESIS_FILE.read_text().splitlines():
            if line.strip():
                try:
                    h = json.loads(line)
                    tried.add(h.get("title", ""))
                except json.JSONDecodeError:
                    pass

    # Priority 1: No trades at all → relax filters
    if analysis.total_trades == 0 and analysis.total_skips > 10:
        top_skip = max(analysis.skip_reasons.items(), key=lambda x: x[1], default=None)
        if top_skip:
            if "QUANT" in top_skip[0] or "ADV" in top_skip[0] or "SPREAD" in top_skip[0]:
                title = f"Relax quant threshold: {top_skip[0]}"
                if title not in tried:
                    return Hypothesis(
                        id=datetime.now().strftime("%Y%m%d_%H%M%S"),
                        category="quant_threshold",
                        title=title,
                        rationale=f"Zero trades in period. Top skip: {top_skip[0]} ({top_skip[1]}x). Filter too aggressive.",
                        target_file="src/kindshot/config.py",
                        change_description=f"Lower the threshold causing {top_skip[0]} skips by 20-30%",
                        expected_impact="More trade opportunities, higher signal coverage",
                        backtest_metric="total_trades > 0 AND win_rate >= 40%",
                    )

            if "BUCKET" in top_skip[0]:
                title = f"Expand bucket keywords for skipped signals"
                if title not in tried:
                    return Hypothesis(
                        id=datetime.now().strftime("%Y%m%d_%H%M%S"),
                        category="bucket_keywords",
                        title=title,
                        rationale=f"Zero trades. Many signals skipped at BUCKET stage ({top_skip[1]}x). Keywords may be too narrow.",
                        target_file="src/kindshot/bucket.py",
                        change_description="Add missing positive signal keywords based on skipped headlines",
                        expected_impact="More signals reach LLM decision stage",
                        backtest_metric="actionable_signals increased",
                    )

    # Priority 2: UNKNOWN bucket too large → keyword expansion
    unknown_count = analysis.bucket_stats.get("UNKNOWN", 0)
    if unknown_count > 0 and analysis.total_events > 0:
        unknown_pct = unknown_count / analysis.total_events * 100
        if unknown_pct > 15:
            title = "Reduce UNKNOWN bucket by expanding keyword coverage"
            if title not in tried:
                return Hypothesis(
                    id=datetime.now().strftime("%Y%m%d_%H%M%S"),
                    category="bucket_keywords",
                    title=title,
                    rationale=f"UNKNOWN bucket is {unknown_pct:.0f}% ({unknown_count} events). Missing keywords.",
                    target_file="src/kindshot/bucket.py",
                    change_description="Analyze UNKNOWN headlines and add appropriate keywords to POS/NEG/IGNORE buckets",
                    expected_impact=f"Reduce UNKNOWN from {unknown_pct:.0f}% to <10%",
                    backtest_metric="unknown_pct < 10%",
                )

    # Priority 3: Low win rate → improve LLM prompt
    if analysis.total_trades > 3:
        win_rate = analysis.profitable_trades / analysis.total_trades * 100
        if win_rate < 50:
            title = f"Improve LLM decision prompt (current win rate: {win_rate:.0f}%)"
            if title not in tried:
                return Hypothesis(
                    id=datetime.now().strftime("%Y%m%d_%H%M%S"),
                    category="llm_prompt",
                    title=title,
                    rationale=f"Win rate {win_rate:.0f}% is below target 50%. LLM prompt may need better context or criteria.",
                    target_file="src/kindshot/decision.py",
                    change_description="Refine LLM prompt: add market regime awareness, tighten BUY criteria, improve context structure",
                    expected_impact="Win rate > 50%",
                    backtest_metric="win_rate >= 50% AND avg_ret_close > 0",
                )

    # Priority 4: Negative avg return → tighten risk or improve signal
    if analysis.avg_ret_close < -0.5 and analysis.total_trades > 2:
        title = f"Tighten guardrails (avg close return: {analysis.avg_ret_close:.2f}%)"
        if title not in tried:
            return Hypothesis(
                id=datetime.now().strftime("%Y%m%d_%H%M%S"),
                category="guardrail",
                title=title,
                rationale=f"Average return at close is {analysis.avg_ret_close:.2f}%. Need stronger risk filters.",
                target_file="src/kindshot/guardrails.py",
                change_description="Add or tighten guardrails: reduce daily loss limit, add intraday stop-loss logic",
                expected_impact="Reduce average loss per trade",
                backtest_metric="avg_ret_close > -0.3%",
            )

    # Priority 5: Missed profitable POS signals
    if analysis.missed_opportunities:
        title = "Reduce false-negative filtering of positive signals"
        if title not in tried:
            return Hypothesis(
                id=datetime.now().strftime("%Y%m%d_%H%M%S"),
                category="quant_threshold",
                title=title,
                rationale=f"{len(analysis.missed_opportunities)} POS signals filtered by QUANT/GUARDRAIL.",
                target_file="src/kindshot/config.py",
                change_description="Review and relax specific thresholds that blocked positive signals",
                expected_impact="Capture more profitable signals",
                backtest_metric="profitable_trades increased",
            )

    return None


def run_improvement_cycle(analysis: PerformanceAnalysis, hypothesis: Hypothesis) -> bool:
    """Execute one improvement cycle: implement → test → backtest → deploy/revert."""

    print(f"\n{'='*60}")
    print(f"EXECUTING: {hypothesis.title}")
    print(f"Category: {hypothesis.category}")
    print(f"Target: {hypothesis.target_file}")
    print(f"{'='*60}\n")

    # Save hypothesis
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(HYPOTHESIS_FILE, "a") as f:
        f.write(json.dumps({
            "id": hypothesis.id,
            "category": hypothesis.category,
            "title": hypothesis.title,
            "rationale": hypothesis.rationale,
            "timestamp": datetime.now().isoformat(),
            "status": "executing",
        }) + "\n")

    # Build prompt for opendev
    analysis_text = format_analysis(analysis)
    prompt = f"""You are improving the Kindshot automated trading system.
Your goal is to INCREASE PROFITABILITY based on actual performance data.

## Current Performance Analysis
{analysis_text}

## Hypothesis to Implement
**Title**: {hypothesis.title}
**Category**: {hypothesis.category}
**Rationale**: {hypothesis.rationale}
**Target file**: {hypothesis.target_file}
**Change**: {hypothesis.change_description}
**Success metric**: {hypothesis.backtest_metric}

## Rules
- Make ONE focused change to {hypothesis.target_file}
- Keep the change small and reversible
- Do NOT change deploy/, secrets, or .env
- Add/update tests for the change
- Run: python -m pytest tests/ -x -q
- Base your changes on the ACTUAL PERFORMANCE DATA above, not assumptions
- If adjusting thresholds, make conservative changes (10-20% at a time)

## Domain Context
- Kindshot trades Korean stocks based on KIS disclosure news
- Bucket classification → Quant filter → LLM decision → Guardrails → Trade
- Profitability comes from: catching real positive signals early, avoiding noise
- Key levers: bucket keywords, quant thresholds, LLM prompt quality, guardrail params

## Implementation Steps
1. Read {hypothesis.target_file} to understand current state
2. Read recent logs for concrete examples (logs/kindshot_*.jsonl)
3. Implement the specific change described above
4. Add/update tests
5. Run pytest to verify no regressions
"""

    # Execute via opendev
    log_file = MEMORY_DIR / f"cycle_{hypothesis.id}.log"
    try:
        result = subprocess.run(
            [OPENDEV_CMD, "-p", prompt, "-d", str(PROJECT_DIR),
             "--dangerously-skip-permissions"],
            capture_output=False,
            timeout=600,
        )
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        print("ERROR: opendev timed out after 10 minutes")
        return False
    except FileNotFoundError:
        print(f"ERROR: '{OPENDEV_CMD}' not found. Set OPENDEV_CMD env var.")
        return False

    if exit_code != 0:
        print(f"opendev exited with code {exit_code}")
        return False

    # Verify: run tests
    print("\nRunning tests...")
    test_result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-x", "-q"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
    )

    if test_result.returncode != 0:
        print(f"Tests FAILED:\n{test_result.stdout[-500:]}")
        print("Reverting changes...")
        subprocess.run(["git", "checkout", "--", "."], cwd=PROJECT_DIR)
        return False

    print("Tests passed!")

    # Check if there are actual changes
    diff = subprocess.run(
        ["git", "diff", "--stat"], cwd=PROJECT_DIR, capture_output=True, text=True
    )
    if not diff.stdout.strip():
        print("No changes made. Skipping commit.")
        return False

    # Commit
    commit_msg = (
        f"strategy: {hypothesis.title}\n\n"
        f"Category: {hypothesis.category}\n"
        f"Rationale: {hypothesis.rationale}\n"
        f"Target: {hypothesis.target_file}\n"
        f"Expected: {hypothesis.expected_impact}\n\n"
        f"Automated strategy improvement via strategy-improve.py"
    )
    subprocess.run(["git", "add", "-A"], cwd=PROJECT_DIR)
    subprocess.run(["git", "commit", "-m", commit_msg], cwd=PROJECT_DIR)

    print(f"\nCommitted: {hypothesis.title}")

    # Update hypothesis status
    with open(HYPOTHESIS_FILE, "a") as f:
        f.write(json.dumps({
            "id": hypothesis.id,
            "title": hypothesis.title,
            "status": "deployed",
            "timestamp": datetime.now().isoformat(),
        }) + "\n")

    return True


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Kindshot strategy improvement loop")
    parser.add_argument("command", choices=["analyze", "propose", "run"],
                       help="analyze=show performance, propose=suggest next change, run=full cycle")
    parser.add_argument("--loop", type=int, default=1, help="Number of cycles (for run)")
    parser.add_argument("--days", type=int, default=14, help="Days of data to analyze")
    args = parser.parse_args()

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    if args.command == "analyze":
        analysis = analyze_performance(args.days)
        report = format_analysis(analysis)
        print(report)
        # Save
        report_path = MEMORY_DIR / "latest_analysis.md"
        report_path.write_text(report)
        print(f"\nSaved to {report_path}")

    elif args.command == "propose":
        analysis = analyze_performance(args.days)
        hypothesis = propose_hypothesis(analysis)
        if hypothesis:
            print(f"\n{'='*60}")
            print(f"PROPOSED IMPROVEMENT")
            print(f"{'='*60}")
            print(f"Title:    {hypothesis.title}")
            print(f"Category: {hypothesis.category}")
            print(f"Target:   {hypothesis.target_file}")
            print(f"Rationale: {hypothesis.rationale}")
            print(f"Change:   {hypothesis.change_description}")
            print(f"Metric:   {hypothesis.backtest_metric}")
        else:
            print("No improvement hypothesis found. Strategy may be optimal or data insufficient.")

    elif args.command == "run":
        for cycle in range(1, args.loop + 1):
            print(f"\n{'#'*60}")
            print(f"# Strategy Improvement Cycle #{cycle}")
            print(f"{'#'*60}")

            analysis = analyze_performance(args.days)
            hypothesis = propose_hypothesis(analysis)

            if not hypothesis:
                print("No more improvements to propose. Done.")
                break

            success = run_improvement_cycle(analysis, hypothesis)
            if not success:
                print(f"Cycle #{cycle} failed. Stopping.")
                break

            print(f"Cycle #{cycle} complete!")

            if cycle < args.loop:
                import time
                print("Next cycle in 5s... (Ctrl+C to stop)")
                time.sleep(5)


if __name__ == "__main__":
    main()
