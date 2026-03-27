#!/usr/bin/env python3
"""Offline LLM prompt evaluation for Kindshot decision prompts."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from statistics import mean
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kindshot.config import Config
from kindshot.decision import DecisionEngine
from kindshot.hold_profile import resolve_hold_profile
from kindshot.models import Action, Bucket, ContextCard, MarketContext
from kindshot.tz import KST as _KST


def _load_backtest_module():
    path = PROJECT_ROOT / "scripts" / "backtest_analysis.py"
    spec = spec_from_file_location("backtest_analysis", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BACKTEST = _load_backtest_module()
ExitSimulationConfig = _BACKTEST.ExitSimulationConfig
Trade = _BACKTEST.Trade
simulate_trade_exit = _BACKTEST.simulate_trade_exit

CONFIDENCE_BANDS: tuple[tuple[str, int, int], ...] = (
    ("0-59", 0, 59),
    ("60-69", 60, 69),
    ("70-79", 70, 79),
    ("80-89", 80, 89),
    ("90-100", 90, 100),
)


@dataclass(frozen=True)
class EvalCase:
    event_id: str
    date: str
    ticker: str
    corp_name: str
    headline: str
    bucket: str
    keyword_hits: list[str]
    dorg: str
    detected_at: str
    detected_hhmmss: str
    ctx: ContextCard
    market_ctx: MarketContext
    hold_minutes: int
    target_action: str
    exit_pnl_pct: float
    historical_action: str
    historical_confidence: int
    historical_reason: str
    historical_source: str


@dataclass(frozen=True)
class EvalPrediction:
    event_id: str
    action: str
    confidence: int
    size_hint: str
    reason: str
    source: str


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            rows.append(json.loads(line))
    return rows


def _confidence_band(confidence: int) -> str:
    for label, low, high in CONFIDENCE_BANDS:
        if low <= confidence <= high:
            return label
    return "unknown"


def _load_context_index(context_dir: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    if not context_dir.exists():
        return index
    for path in sorted(context_dir.glob("*.jsonl")):
        for row in _read_jsonl(path):
            event_id = str(row.get("event_id", "")).strip()
            if event_id:
                index[event_id] = row
    return index


def _build_trade(
    event: dict[str, Any],
    decision: dict[str, Any],
    snapshot_rows: list[dict[str, Any]],
    runtime_defaults: ExitSimulationConfig,
) -> tuple[Any, int]:
    snapshot_map = {
        str(row.get("horizon")): row
        for row in snapshot_rows
        if str(row.get("horizon"))
    }
    t0_row = snapshot_map.get("t0", {})
    entry_price = float(t0_row.get("px", 0.0) or 0.0)
    if entry_price <= 0:
        raise ValueError("missing t0 snapshot")
    returns = {
        horizon: _BACKTEST._snapshot_return_pct(snapshot_map, horizon, entry_price)
        for horizon in _BACKTEST.HORIZON_ORDER
    }
    returns = {horizon: value for horizon, value in returns.items() if value is not None}
    if not returns:
        raise ValueError("missing return snapshots")
    hold_minutes, hold_keyword = resolve_hold_profile(
        str(event.get("headline", "")),
        list(event.get("keyword_hits") or []),
        runtime_defaults,
    )
    trade = Trade(
        event_id=str(event.get("event_id", "")),
        date=str(event.get("detected_at", ""))[:10].replace("-", ""),
        ticker=str(event.get("ticker", "")),
        headline=str(event.get("headline", "")),
        bucket=str(event.get("bucket", "")),
        confidence=int(decision.get("confidence", 0)),
        size_hint=str(decision.get("size_hint", "S")),
        reason=str(decision.get("reason", "")),
        decision_source=str(decision.get("decision_source", "")),
        detected_at=str(event.get("detected_at", "")),
        source=str(event.get("source", "")),
        dorg=str(event.get("dorg", "")),
        keyword_hits=list(event.get("keyword_hits") or []),
        entry_price=entry_price,
        snapshots=returns,
        hold_profile_minutes=hold_minutes,
        hold_profile_keyword=hold_keyword,
    )
    return simulate_trade_exit(trade, runtime_defaults), hold_minutes


def build_eval_cases(
    log_paths: list[Path],
    *,
    context_dir: Path,
    runtime_defaults: ExitSimulationConfig,
) -> list[EvalCase]:
    events: dict[str, dict[str, Any]] = {}
    decisions: dict[str, dict[str, Any]] = {}
    snapshots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in log_paths:
        for row in _read_jsonl(path):
            row_type = row.get("type", row.get("record_type", ""))
            event_id = str(row.get("event_id", "")).strip()
            if not event_id:
                continue
            if row_type == "event":
                events[event_id] = row
            elif row_type == "decision":
                decisions[event_id] = row
            elif row_type == "price_snapshot":
                snapshots[event_id].append(row)

    context_index = _load_context_index(context_dir)
    cases: list[EvalCase] = []
    for event_id, decision in decisions.items():
        if str(decision.get("decision_source")) != "LLM":
            continue
        event = events.get(event_id)
        if event is None:
            continue
        try:
            exit_result, hold_minutes = _build_trade(
                event,
                decision,
                snapshots.get(event_id, []),
                runtime_defaults,
            )
        except ValueError:
            continue
        ctx_payload = event.get("ctx") or context_index.get(event_id, {}).get("ctx") or {}
        market_payload = event.get("market_ctx") or context_index.get(event_id, {}).get("market_ctx") or {}
        detected_at = str(event.get("detected_at", ""))
        if not detected_at:
            continue
        try:
            detected_dt = datetime.fromisoformat(detected_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        target_action = Action.BUY.value if exit_result.exit_pnl_pct > 0 else Action.SKIP.value
        cases.append(
            EvalCase(
                event_id=event_id,
                date=detected_at[:10].replace("-", ""),
                ticker=str(event.get("ticker", "")),
                corp_name=str(event.get("corp_name", "")),
                headline=str(event.get("headline", "")),
                bucket=str(event.get("bucket", "")),
                keyword_hits=list(event.get("keyword_hits") or []),
                dorg=str(event.get("dorg", "")),
                detected_at=detected_at,
                detected_hhmmss=detected_dt.astimezone(_KST).strftime("%H:%M:%S"),
                ctx=ContextCard(**ctx_payload),
                market_ctx=MarketContext(**market_payload),
                hold_minutes=hold_minutes,
                target_action=target_action,
                exit_pnl_pct=float(exit_result.exit_pnl_pct),
                historical_action=str(decision.get("action", "")),
                historical_confidence=int(decision.get("confidence", 0)),
                historical_reason=str(decision.get("reason", "")),
                historical_source=str(decision.get("decision_source", "")),
            )
        )
    return sorted(cases, key=lambda row: row.detected_at, reverse=True)


def select_cases(cases: list[EvalCase], max_cases: int) -> list[EvalCase]:
    if max_cases <= 0 or len(cases) <= max_cases:
        return cases
    buy_cases = [case for case in cases if case.target_action == Action.BUY.value]
    skip_cases = [case for case in cases if case.target_action == Action.SKIP.value]
    selected: list[EvalCase] = []
    selected.extend(buy_cases[: max_cases // 2])
    selected.extend(skip_cases[: max_cases - len(selected)])
    seen = {case.event_id for case in selected}
    for case in cases:
        if len(selected) >= max_cases:
            break
        if case.event_id in seen:
            continue
        selected.append(case)
        seen.add(case.event_id)
    return sorted(selected, key=lambda row: row.detected_at, reverse=True)


def score_predictions(cases: list[EvalCase], predictions: list[EvalPrediction]) -> dict[str, Any]:
    pred_by_id = {prediction.event_id: prediction for prediction in predictions}
    matched = [case for case in cases if case.event_id in pred_by_id]
    action_counts: Counter[str] = Counter()
    target_counts: Counter[str] = Counter(case.target_action for case in matched)
    confidence_band_counts: Counter[str] = Counter()
    confidence_band_correct: Counter[str] = Counter()
    confidence_band_values: defaultdict[str, list[int]] = defaultdict(list)
    chosen_buy_pnls: list[float] = []
    brier_terms: list[float] = []
    correct_flags: list[bool] = []

    for case in matched:
        prediction = pred_by_id[case.event_id]
        correct = prediction.action == case.target_action
        correct_flags.append(correct)
        action_counts[prediction.action] += 1
        band = _confidence_band(prediction.confidence)
        confidence_band_counts[band] += 1
        confidence_band_values[band].append(prediction.confidence)
        if correct:
            confidence_band_correct[band] += 1
        predicted_prob = max(0.0, min(1.0, prediction.confidence / 100.0))
        brier_terms.append((predicted_prob - (1.0 if correct else 0.0)) ** 2)
        if prediction.action == Action.BUY.value:
            chosen_buy_pnls.append(case.exit_pnl_pct)

    predicted_buy = max(1, action_counts[Action.BUY.value])
    predicted_skip = max(1, action_counts[Action.SKIP.value])
    target_buy = max(1, target_counts[Action.BUY.value])
    buy_correct = sum(
        1
        for case in matched
        if pred_by_id[case.event_id].action == Action.BUY.value and case.target_action == Action.BUY.value
    )
    skip_correct = sum(
        1
        for case in matched
        if pred_by_id[case.event_id].action == Action.SKIP.value and case.target_action == Action.SKIP.value
    )

    return {
        "case_count": len(matched),
        "accuracy": round(sum(correct_flags) / max(1, len(correct_flags)), 4),
        "buy_precision": round(buy_correct / predicted_buy, 4),
        "skip_precision": round(skip_correct / predicted_skip, 4),
        "buy_recall": round(buy_correct / target_buy, 4),
        "false_negative_rate": round(
            sum(
                1
                for case in matched
                if pred_by_id[case.event_id].action == Action.SKIP.value and case.target_action == Action.BUY.value
            ) / target_buy,
            4,
        ),
        "avg_exit_pnl_for_predicted_buy": round(mean(chosen_buy_pnls), 4) if chosen_buy_pnls else None,
        "mean_confidence": round(mean(pred_by_id[case.event_id].confidence for case in matched), 2) if matched else None,
        "brier_score": round(mean(brier_terms), 4) if brier_terms else None,
        "action_counts": dict(action_counts),
        "target_counts": dict(target_counts),
        "confidence_calibration": {
            band: {
                "count": confidence_band_counts[band],
                "mean_confidence": round(mean(confidence_band_values[band]), 2),
                "correct_rate": round(confidence_band_correct[band] / confidence_band_counts[band], 4),
            }
            for band in confidence_band_counts
        },
    }


def historical_predictions(cases: list[EvalCase]) -> list[EvalPrediction]:
    return [
        EvalPrediction(
            event_id=case.event_id,
            action=case.historical_action,
            confidence=case.historical_confidence,
            size_hint="S",
            reason=case.historical_reason,
            source=case.historical_source,
        )
        for case in cases
    ]


async def replay_prompt_predictions(
    cases: list[EvalCase],
    *,
    prompt_path: Path,
    config: Config,
) -> list[EvalPrediction]:
    strategy_text = prompt_path.read_text(encoding="utf-8")
    engine = DecisionEngine(config)
    predictions: list[EvalPrediction] = []
    for case in cases:
        decision = await engine.decide(
            ticker=case.ticker,
            corp_name=case.corp_name,
            headline=case.headline,
            bucket=Bucket(case.bucket),
            ctx=case.ctx,
            detected_at_str=case.detected_hhmmss,
            keyword_hits=case.keyword_hits,
            analysis_headline=case.headline,
            dorg=case.dorg,
            run_id=f"prompt_eval_{case.event_id}",
            schema_version=config.schema_version,
            market_ctx=case.market_ctx,
            strategy_override=strategy_text,
        )
        predictions.append(
            EvalPrediction(
                event_id=case.event_id,
                action=decision.action.value,
                confidence=decision.confidence,
                size_hint=decision.size_hint.value,
                reason=decision.reason,
                source=decision.decision_source,
            )
        )
    return predictions


def count_fast_profile_late_cases(cases: list[EvalCase], config: Config) -> dict[str, int]:
    total = sum(1 for case in cases if case.hold_minutes == config.fast_profile_hold_minutes)
    late = sum(
        1
        for case in cases
        if case.hold_minutes == config.fast_profile_hold_minutes
        and int(case.detected_hhmmss[:2]) >= config.fast_profile_no_buy_after_kst_hour
    )
    return {
        "fast_profile_case_count": total,
        "fast_profile_late_case_count": late,
    }


def _render_text(payload: dict[str, Any]) -> str:
    lines = [
        "LLM Prompt Eval",
        (
            f"cases={payload['cases']['count']} "
            f"buy_target={payload['cases']['buy_target_count']} "
            f"skip_target={payload['cases']['skip_target_count']}"
        ),
        (
            "cost_candidates="
            f"{payload['cost_candidates']['fast_profile_late_case_count']}/"
            f"{payload['cost_candidates']['fast_profile_case_count']} fast-profile cases"
        ),
    ]
    for name, metrics in payload["runs"].items():
        lines.append("")
        lines.append(f"[{name}]")
        if metrics.get("status") == "error":
            lines.append(f"status=error error={metrics.get('error', '')}")
            continue
        lines.append(
            "accuracy={accuracy:.3f} buy_precision={buy_precision:.3f} "
            "skip_precision={skip_precision:.3f} buy_recall={buy_recall:.3f} "
            "fnr={false_negative_rate:.3f} brier={brier_score}".format(**metrics)
        )
        lines.append(
            f"action_counts={metrics['action_counts']} avg_buy_exit={metrics['avg_exit_pnl_for_predicted_buy']}"
        )
    return "\n".join(lines)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


async def _amain(args: argparse.Namespace) -> dict[str, Any]:
    log_dir = Path(args.log_dir)
    log_paths = [log_dir / f"kindshot_{date}.jsonl" for date in args.dates] if args.dates else sorted(log_dir.glob("kindshot_*.jsonl"))
    runtime_defaults = ExitSimulationConfig.from_runtime_defaults()
    cases = build_eval_cases(
        log_paths,
        context_dir=Path(args.context_dir),
        runtime_defaults=runtime_defaults,
    )
    selected_cases = select_cases(cases, args.max_cases)
    config = Config()
    payload: dict[str, Any] = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "log_paths": log_paths,
            "context_dir": Path(args.context_dir),
            "max_cases": args.max_cases,
        },
        "cases": {
            "count": len(selected_cases),
            "buy_target_count": sum(1 for case in selected_cases if case.target_action == Action.BUY.value),
            "skip_target_count": sum(1 for case in selected_cases if case.target_action == Action.SKIP.value),
            "event_ids": [case.event_id for case in selected_cases],
        },
        "cost_candidates": count_fast_profile_late_cases(selected_cases, config),
        "runs": {},
    }
    payload["runs"]["historical_actual"] = score_predictions(selected_cases, historical_predictions(selected_cases))

    for prompt in args.prompt:
        prompt_path = Path(prompt)
        try:
            predictions = await replay_prompt_predictions(selected_cases, prompt_path=prompt_path, config=config)
        except Exception as exc:
            payload["runs"][prompt_path.stem] = {
                "status": "error",
                "error": str(exc)[:200],
            }
            continue
        payload["runs"][prompt_path.stem] = score_predictions(selected_cases, predictions)

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--context-dir", default="data/runtime/context_cards")
    parser.add_argument("--dates", nargs="*")
    parser.add_argument("--max-cases", type=int, default=20)
    parser.add_argument("--prompt", action="append", default=[], help="Prompt file path(s) to replay")
    parser.add_argument("--output")
    parser.add_argument("--format", choices=("text", "json", "both"), default="text")
    args = parser.parse_args()

    payload = asyncio.run(_amain(args))
    text_report = _render_text(payload)
    json_report = json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default)

    if args.output:
        output = Path(args.output)
        if args.format in ("text", "both"):
            text_path = output if output.suffix else output.with_suffix(".txt")
            text_path.write_text(text_report, encoding="utf-8")
        if args.format in ("json", "both"):
            json_path = output if output.suffix == ".json" else output.with_suffix(".json")
            json_path.write_text(json_report, encoding="utf-8")
    else:
        if args.format in ("text", "both"):
            print(text_report)
        if args.format in ("json", "both"):
            if args.format == "both":
                print("")
            print(json_report)


if __name__ == "__main__":
    main()
