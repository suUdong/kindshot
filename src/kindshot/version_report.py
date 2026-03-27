"""버전별 성과 비교 리포트 자동 생성."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kindshot.trade_db import TradeDB, VERSION_PARAMS, simulate_version_on_trades

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VersionMetrics:
    """한 버전의 성과 지표."""
    version: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_ret_pct: float
    total_ret_pct: float
    profit_factor: float
    max_win_pct: float
    max_loss_pct: float
    avg_confidence: float
    avg_peak_pct: float
    mdd_pct: float
    description: str


def _calc_profit_factor(results: list[dict[str, Any]]) -> float:
    gross_profit = sum(r["exit_ret_pct"] for r in results if (r.get("exit_ret_pct") or 0) > 0)
    gross_loss = abs(sum(r["exit_ret_pct"] for r in results if (r.get("exit_ret_pct") or 0) <= 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return round(gross_profit / gross_loss, 2)


def _calc_mdd(results: list[dict[str, Any]]) -> float:
    """최대 낙폭 계산 (누적 수익률 기준)."""
    if not results:
        return 0.0
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for r in sorted(results, key=lambda x: x.get("date", "")):
        ret = r.get("exit_ret_pct") or 0.0
        cum += ret
        peak = max(peak, cum)
        dd = cum - peak
        mdd = min(mdd, dd)
    return round(mdd, 4)


def generate_version_comparison(db: TradeDB) -> list[VersionMetrics]:
    """모든 버전에 대해 동일한 트레이드 셋으로 시뮬레이션 비교."""
    from kindshot.trade_db import VERSION_MAP

    all_metrics: list[VersionMetrics] = []

    for version_tag in VERSION_PARAMS:
        desc = ""
        for vm in VERSION_MAP:
            if vm["tag"] == version_tag:
                desc = vm["description"]
                break

        results = simulate_version_on_trades(db, version_tag)
        valid = [r for r in results if r.get("exit_ret_pct") is not None]

        if not valid:
            all_metrics.append(VersionMetrics(
                version=version_tag, total_trades=0, wins=0, losses=0,
                win_rate=0.0, avg_ret_pct=0.0, total_ret_pct=0.0,
                profit_factor=0.0, max_win_pct=0.0, max_loss_pct=0.0,
                avg_confidence=0.0, avg_peak_pct=0.0, mdd_pct=0.0,
                description=desc,
            ))
            continue

        wins = [r for r in valid if (r["exit_ret_pct"] or 0) > 0]
        losses = [r for r in valid if (r["exit_ret_pct"] or 0) <= 0]
        rets = [r["exit_ret_pct"] for r in valid]
        confs = [r.get("confidence") or 0 for r in valid]

        all_metrics.append(VersionMetrics(
            version=version_tag,
            total_trades=len(valid),
            wins=len(wins),
            losses=len(losses),
            win_rate=round(len(wins) / len(valid) * 100, 1) if valid else 0.0,
            avg_ret_pct=round(sum(rets) / len(rets), 4) if rets else 0.0,
            total_ret_pct=round(sum(rets), 4),
            profit_factor=_calc_profit_factor(valid),
            max_win_pct=round(max(rets), 4) if rets else 0.0,
            max_loss_pct=round(min(rets), 4) if rets else 0.0,
            avg_confidence=round(sum(confs) / len(confs), 1) if confs else 0.0,
            avg_peak_pct=0.0,  # 시뮬레이션에서는 peak 없음
            mdd_pct=_calc_mdd(valid),
            description=desc,
        ))

    return all_metrics


def generate_actual_version_report(db: TradeDB) -> list[VersionMetrics]:
    """실제 배포 기준 버전별 성과 (DB에 저장된 version_tag 기준)."""
    from kindshot.trade_db import VERSION_MAP

    rows = db.version_summary()
    metrics = []
    for row in rows:
        tag = row["version_tag"]
        total = row["total_trades"]
        wins = row["wins"]
        desc = ""
        for vm in VERSION_MAP:
            if vm["tag"] == tag:
                desc = vm["description"]
                break

        # profit factor 계산
        trades_data = db.query(
            "SELECT exit_ret_pct FROM trades WHERE version_tag = ? AND exit_ret_pct IS NOT NULL",
            (tag,),
        )
        pf = _calc_profit_factor(trades_data)
        mdd = _calc_mdd(trades_data)

        metrics.append(VersionMetrics(
            version=tag,
            total_trades=total,
            wins=wins,
            losses=row["losses"],
            win_rate=round(wins / total * 100, 1) if total else 0.0,
            avg_ret_pct=row["avg_ret_pct"],
            total_ret_pct=row["total_ret_pct"],
            profit_factor=pf,
            max_win_pct=row["max_win_pct"],
            max_loss_pct=row["max_loss_pct"],
            avg_confidence=row["avg_confidence"],
            avg_peak_pct=row.get("avg_peak_pct", 0.0),
            mdd_pct=mdd,
            description=desc,
        ))

    return metrics


def report_to_json(metrics: list[VersionMetrics]) -> str:
    """VersionMetrics 리스트를 JSON으로 변환."""
    rows = []
    for m in metrics:
        rows.append({
            "version": m.version,
            "total_trades": m.total_trades,
            "wins": m.wins,
            "losses": m.losses,
            "win_rate": m.win_rate,
            "avg_ret_pct": m.avg_ret_pct,
            "total_ret_pct": m.total_ret_pct,
            "profit_factor": m.profit_factor,
            "max_win_pct": m.max_win_pct,
            "max_loss_pct": m.max_loss_pct,
            "avg_confidence": m.avg_confidence,
            "mdd_pct": m.mdd_pct,
            "description": m.description,
        })
    return json.dumps(rows, ensure_ascii=False, indent=2)


def report_to_text(metrics: list[VersionMetrics]) -> str:
    """VersionMetrics 리스트를 텍스트 테이블로 변환."""
    if not metrics:
        return "No version data available.\n"

    lines = [
        "=" * 90,
        "버전별 성과 비교 리포트",
        "=" * 90,
        f"{'버전':<10} {'거래수':>6} {'승률':>7} {'평균수익':>9} {'총수익':>9} {'PF':>6} {'MDD':>8} {'설명'}",
        "-" * 90,
    ]
    for m in metrics:
        pf_str = f"{m.profit_factor:.2f}" if m.profit_factor != float("inf") else "∞"
        lines.append(
            f"{m.version:<10} {m.total_trades:>6} {m.win_rate:>6.1f}% "
            f"{m.avg_ret_pct:>+8.2f}% {m.total_ret_pct:>+8.2f}% "
            f"{pf_str:>6} {m.mdd_pct:>+7.2f}% {m.description}"
        )
    lines.append("=" * 90)

    # 베스트 버전 하이라이트
    valid = [m for m in metrics if m.total_trades > 0]
    if valid:
        best_wr = max(valid, key=lambda m: m.win_rate)
        best_pnl = max(valid, key=lambda m: m.total_ret_pct)
        lines.append(f"\n최고 승률: {best_wr.version} ({best_wr.win_rate:.1f}%)")
        lines.append(f"최고 수익: {best_pnl.version} ({best_pnl.total_ret_pct:+.2f}%)")

    return "\n".join(lines) + "\n"


def save_report(
    db: TradeDB,
    output_dir: Path,
    *,
    simulated: bool = True,
) -> Path:
    """리포트 생성 후 파일로 저장."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if simulated:
        metrics = generate_version_comparison(db)
        prefix = "version_comparison_simulated"
    else:
        metrics = generate_actual_version_report(db)
        prefix = "version_comparison_actual"

    # JSON
    json_path = output_dir / f"{prefix}.json"
    json_path.write_text(report_to_json(metrics), encoding="utf-8")

    # Text
    text_path = output_dir / f"{prefix}.txt"
    text_path.write_text(report_to_text(metrics), encoding="utf-8")

    logger.info("Version report saved: %s, %s", json_path, text_path)
    return json_path
