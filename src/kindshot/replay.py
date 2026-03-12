"""Replay mode: re-run LLM decisions on previously logged events."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from kindshot.config import Config
from kindshot.decision import DecisionEngine, LlmTimeoutError, LlmCallError, LlmParseError
from kindshot.guardrails import check_guardrails
from kindshot.logger import JsonlLogger
from kindshot.models import (
    Action,
    Bucket,
    ContextCard,
    DecisionRecord,
    EventRecord,
)

logger = logging.getLogger(__name__)


def _summarize_returns(returns: list[float]) -> dict[str, float]:
    """Summarize trade returns with simple risk-aware metrics."""
    if not returns:
        return {}

    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]

    equity = 1.0
    peak = 1.0
    max_drawdown_pct = 0.0
    for ret_pct in returns:
        equity *= 1 + ret_pct / 100
        peak = max(peak, equity)
        drawdown_pct = (equity / peak - 1) * 100
        max_drawdown_pct = min(max_drawdown_pct, drawdown_pct)

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return {
        "trade_count": float(len(returns)),
        "win_rate_pct": len(wins) / len(returns) * 100,
        "avg_return_pct": sum(returns) / len(returns),
        "avg_win_pct": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss_pct": sum(losses) / len(losses) if losses else 0.0,
        "best_pct": max(returns),
        "worst_pct": min(returns),
        "max_drawdown_pct": max_drawdown_pct,
        "profit_factor": profit_factor,
    }


def _load_actionable_events(log_path: Path) -> list[dict]:
    """Load POS_STRONG events that passed quant from a JSONL log file (deduped by event_id)."""
    seen_ids: set[str] = set()
    events: list[dict] = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("type") != "event":
                continue
            if rec.get("bucket") != "POS_STRONG":
                continue
            if rec.get("quant_check_passed") is not True:
                continue
            eid = rec.get("event_id")
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            events.append(rec)
    return events


def _load_price_snapshots(log_path: Path) -> dict[str, dict[str, dict]]:
    """Load price_snapshot records keyed by event_id → horizon → snapshot data."""
    snapshots: dict[str, dict[str, dict]] = {}
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("type") != "price_snapshot":
                continue
            eid = rec.get("event_id", "")
            horizon = rec.get("horizon", "")
            if eid and horizon:
                snapshots.setdefault(eid, {})[horizon] = rec
    return snapshots


async def _fetch_post_hoc_prices(ticker: str, date_str: str) -> dict:
    """Fetch post-hoc daily prices via pykrx for a given date."""

    def _fetch() -> dict:
        try:
            from pykrx import stock

            df = stock.get_market_ohlcv(date_str, date_str, ticker)
            if df.empty:
                return {}
            row = df.iloc[0]
            return {
                "open": int(row["시가"]),
                "high": int(row["고가"]),
                "low": int(row["저가"]),
                "close": int(row["종가"]),
                "volume": int(row["거래량"]),
            }
        except Exception:
            logger.exception("pykrx post-hoc fetch failed for %s on %s", ticker, date_str)
            return {}

    return await asyncio.to_thread(_fetch)


async def replay(log_path: Path, config: Config) -> None:
    """Replay logged events through LLM decision + post-hoc price analysis."""
    events = _load_actionable_events(log_path)
    if not events:
        logger.info("No actionable events found in %s", log_path)
        return

    # Load price snapshots from same log for t0/close return calculation
    price_snapshots = _load_price_snapshots(log_path)

    logger.info("Replay: %d actionable events from %s", len(events), log_path)

    engine = DecisionEngine(config)
    replay_log = JsonlLogger(config.log_dir, run_id=f"replay_{datetime.now().strftime('%Y%m%d_%H%M%S')}", file_prefix="replay")

    stats = {"total": 0, "buy": 0, "skip": 0, "error": 0, "returns": []}

    for rec in events:
        stats["total"] += 1
        ticker = rec["ticker"]
        headline = rec["headline"]
        corp_name = rec["corp_name"]
        event_id = rec.get("event_id", "")

        # Restore context card from logged data
        ctx_data = rec.get("ctx") or {}
        ctx = ContextCard(**{k: v for k, v in ctx_data.items() if k in ContextCard.model_fields})

        detected_at_str = rec.get("detected_at", "")
        if detected_at_str:
            try:
                from datetime import timedelta, timezone as tz
                _KST = tz(timedelta(hours=9))
                dt = datetime.fromisoformat(detected_at_str)
                # Convert to KST to match live pipeline's KST prompt labeling
                dt_kst = dt.astimezone(_KST)
                detected_at_str = dt_kst.strftime("%H:%M:%S")
            except (ValueError, TypeError):
                detected_at_str = "09:00:00"

        # LLM decision
        try:
            decision = await engine.decide(
                ticker=ticker,
                corp_name=corp_name,
                headline=headline,
                bucket=Bucket.POS_STRONG,
                ctx=ctx,
                detected_at_str=detected_at_str,
                run_id="replay",
                schema_version=config.schema_version,
            )
        except (LlmTimeoutError, LlmCallError, LlmParseError) as e:
            logger.warning("Replay LLM error for %s: %s", ticker, e)
            stats["error"] += 1
            continue

        decision.event_id = event_id
        decision.mode = "replay"

        # Guardrail
        gr = check_guardrails(
            ticker=ticker,
            config=config,
            spread_bps=ctx.spread_bps,
            adv_value_20d=ctx.adv_value_20d,
            ret_today=ctx.ret_today,
            intraday_value_vs_adv20d=ctx.intraday_value_vs_adv20d,
            quote_temp_stop=ctx.quote_temp_stop,
            quote_liquidation_trade=ctx.quote_liquidation_trade,
            top_ask_notional=ctx.top_ask_notional,
            decision_action=Action(decision.action.value),
        )
        if not gr.passed:
            logger.info("Replay GUARDRAIL block %s: %s", ticker, gr.reason)
            stats["skip"] += 1
            continue

        await replay_log.write(decision)

        if decision.action.value == "BUY":
            stats["buy"] += 1

            # Try price_snapshot t0/close first (most accurate)
            event_snaps = price_snapshots.get(event_id, {})
            t0_snap = event_snaps.get("t0")
            close_snap = event_snaps.get("close")

            if t0_snap and close_snap and t0_snap.get("px") and close_snap.get("px"):
                t0_px = t0_snap["px"]
                close_px = close_snap["px"]
                if t0_px > 0:
                    close_ret = (close_px - t0_px) / t0_px * 100
                    stats["returns"].append({
                        "ticker": ticker,
                        "headline": headline[:40],
                        "entry": t0_px,
                        "close": close_px,
                        "close_ret_pct": round(close_ret, 2),
                        "confidence": decision.confidence,
                        "price_source": "price_snapshot",
                    })
            else:
                # Fallback: pykrx open→close (less accurate)
                disclosed_at = rec.get("disclosed_at") or rec.get("detected_at")
                if disclosed_at:
                    try:
                        dt = datetime.fromisoformat(disclosed_at)
                        date_str = dt.strftime("%Y%m%d")
                        prices = await _fetch_post_hoc_prices(ticker, date_str)
                        if prices and prices.get("close") and prices.get("open"):
                            entry = prices["open"]
                            if entry > 0:
                                close_ret = (prices["close"] - entry) / entry * 100
                                stats["returns"].append({
                                    "ticker": ticker,
                                    "headline": headline[:40],
                                    "entry": entry,
                                    "close": prices["close"],
                                    "close_ret_pct": round(close_ret, 2),
                                    "confidence": decision.confidence,
                                    "price_source": "pykrx_ohlcv",
                                })
                    except (ValueError, TypeError):
                        pass
        else:
            stats["skip"] += 1

    # Summary
    print("\n" + "=" * 60)
    print(f"REPLAY SUMMARY: {log_path.name}")
    print("=" * 60)
    print(f"Total actionable events: {stats['total']}")
    print(f"BUY decisions: {stats['buy']}")
    print(f"SKIP decisions: {stats['skip']}")
    print(f"LLM errors: {stats['error']}")

    if stats["returns"]:
        rets = [r["close_ret_pct"] for r in stats["returns"]]
        summary = _summarize_returns(rets)
        snapshot_count = sum(1 for r in stats["returns"] if r["price_source"] == "price_snapshot")
        fallback_count = len(rets) - snapshot_count
        print(f"\n--- BUY P&L (close vs entry) ---")
        print(f"Trades with price data: {len(rets)} (snapshot: {snapshot_count}, pykrx fallback: {fallback_count})")
        print(f"Win rate: {summary['win_rate_pct']:.0f}%")
        print(f"Avg return: {summary['avg_return_pct']:.2f}%")
        print(f"Avg win / loss: {summary['avg_win_pct']:.2f}% / {summary['avg_loss_pct']:.2f}%")
        print(f"Best: {summary['best_pct']:.2f}%  Worst: {summary['worst_pct']:.2f}%")
        print(f"Max drawdown: {summary['max_drawdown_pct']:.2f}%")
        pf = summary["profit_factor"]
        pf_text = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"Profit factor: {pf_text}")

        print(f"\nDetail:")
        for r in sorted(stats["returns"], key=lambda x: x["close_ret_pct"], reverse=True):
            src = "snap" if r["price_source"] == "price_snapshot" else "ohlcv"
            print(f"  {r['ticker']} {r['headline']} | conf={r['confidence']} "
                  f"entry={r['entry']:,.0f} close={r['close']:,.0f} ret={r['close_ret_pct']:+.2f}% [{src}]")
    else:
        print("\nNo BUY trades with price data available.")
    print("=" * 60)
