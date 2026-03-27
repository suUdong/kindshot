"""성과 추적 모듈 — 일일 PnL, 승률, 전략별 성과 자동 기록."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    ticker: str
    entry_px: float
    exit_px: float
    pnl_pct: float
    event_id: str = ""
    size_won: float = 0.0
    pnl_won: float = 0.0
    hold_seconds: int = 0
    exit_type: str = ""  # TP, SL, TRAILING, TIMEOUT, MANUAL
    confidence: int = 0
    bucket: str = ""
    position_closed: bool = True
    remaining_size_won: float = 0.0
    initial_size_won: float = 0.0
    exit_fraction: float = 1.0
    cumulative_pnl_won: float = 0.0
    cumulative_ret_pct: float = 0.0
    timestamp: str = ""


@dataclass
class DailySummary:
    date: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl_pct: float = 0.0
    total_pnl_won: float = 0.0
    avg_pnl_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    max_win_pct: float = 0.0
    max_loss_pct: float = 0.0
    trades: list[TradeRecord] = field(default_factory=list)


class PerformanceTracker:
    """일일 거래 성과 추적 및 자동 기록."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir / "performance"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._trades: list[TradeRecord] = []
        self._current_date: str = datetime.now(_KST).strftime("%Y-%m-%d")

    def _sync_current_date(self) -> None:
        today = datetime.now(_KST).strftime("%Y-%m-%d")
        if today == self._current_date:
            return
        if self._trades:
            self._flush_daily()
        self._current_date = today

    def summary_path(self) -> Path:
        self._sync_current_date()
        return self._data_dir / f"{self._current_date}_summary.json"

    def _build_daily_summary(self) -> DailySummary:
        trades = self._trades
        total = len(trades)
        if total == 0:
            return DailySummary(date=self._current_date)

        wins = [t for t in trades if t.pnl_pct > 0]
        losses = [t for t in trades if t.pnl_pct <= 0]
        win_count = len(wins)
        loss_count = len(losses)

        total_pnl_pct = sum(t.pnl_pct for t in trades)
        total_pnl_won = sum(t.pnl_won for t in trades)
        avg_pnl = total_pnl_pct / total
        avg_win = sum(t.pnl_pct for t in wins) / win_count if win_count else 0.0
        avg_loss = sum(t.pnl_pct for t in losses) / loss_count if loss_count else 0.0

        gross_profit = sum(t.pnl_pct for t in wins)
        gross_loss = abs(sum(t.pnl_pct for t in losses))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

        return DailySummary(
            date=self._current_date,
            total_trades=total,
            wins=win_count,
            losses=loss_count,
            win_rate=(win_count / total * 100) if total > 0 else 0.0,
            total_pnl_pct=round(total_pnl_pct, 4),
            total_pnl_won=round(total_pnl_won, 0),
            avg_pnl_pct=round(avg_pnl, 4),
            avg_win_pct=round(avg_win, 4),
            avg_loss_pct=round(avg_loss, 4),
            profit_factor=round(pf, 2),
            max_win_pct=round(max((t.pnl_pct for t in trades), default=0.0), 4),
            max_loss_pct=round(min((t.pnl_pct for t in trades), default=0.0), 4),
            trades=list(trades),
        )

    def record_trade(
        self,
        *args: object,
        event_id: str = "",
        size_won: float = 0.0,
        hold_seconds: int = 0,
        exit_type: str = "",
        confidence: int = 0,
        bucket: str = "",
        position_closed: bool = True,
        remaining_size_won: float = 0.0,
        initial_size_won: float = 0.0,
        exit_fraction: float = 1.0,
        cumulative_pnl_won: float | None = None,
        cumulative_ret_pct: float | None = None,
    ) -> TradeRecord:
        """거래 결과 기록."""
        self._sync_current_date()

        if len(args) == 5:
            event_id = str(args[0])
            ticker = str(args[1])
            entry_px = float(args[2])
            exit_px = float(args[3])
            pnl_pct = float(args[4])
        elif len(args) == 4:
            ticker = str(args[0])
            entry_px = float(args[1])
            exit_px = float(args[2])
            pnl_pct = float(args[3])
        else:
            raise TypeError("record_trade expects either (ticker, entry_px, exit_px, pnl_pct) or (event_id, ticker, entry_px, exit_px, pnl_pct)")

        pnl_won = size_won * (pnl_pct / 100) if size_won > 0 else 0.0
        record = TradeRecord(
            event_id=event_id,
            ticker=ticker,
            entry_px=entry_px,
            exit_px=exit_px,
            pnl_pct=pnl_pct,
            size_won=size_won,
            pnl_won=pnl_won,
            hold_seconds=hold_seconds,
            exit_type=exit_type,
            confidence=confidence,
            bucket=bucket,
            position_closed=position_closed,
            remaining_size_won=remaining_size_won,
            initial_size_won=initial_size_won,
            exit_fraction=exit_fraction,
            cumulative_pnl_won=pnl_won if cumulative_pnl_won is None else cumulative_pnl_won,
            cumulative_ret_pct=pnl_pct if cumulative_ret_pct is None else cumulative_ret_pct,
            timestamp=datetime.now(_KST).isoformat(),
        )
        self._trades.append(record)

        # Append to daily JSONL immediately
        self._append_trade_log(record)
        return record

    def daily_summary(self) -> DailySummary:
        """현재 일일 요약 계산."""
        self._sync_current_date()
        return self._build_daily_summary()

    def live_metrics(self) -> dict[str, float | int]:
        """현재 intraday closed-trade 기준 실시간 성과 메트릭."""
        self._sync_current_date()
        trades = self._trades
        if not trades:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_pnl_pct": 0.0,
                "total_pnl_won": 0.0,
                "avg_pnl_pct": 0.0,
                "peak_ret_pct": 0.0,
                "mdd_pct": 0.0,
            }

        wins = [t for t in trades if t.pnl_pct > 0]
        losses = [t for t in trades if t.pnl_pct <= 0]
        cumulative = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for trade in trades:
            cumulative += trade.pnl_pct
            peak = max(peak, cumulative)
            max_drawdown = min(max_drawdown, cumulative - peak)

        total_trades = len(trades)
        total_pnl_pct = sum(t.pnl_pct for t in trades)
        total_pnl_won = sum(t.pnl_won for t in trades)
        return {
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round((len(wins) / total_trades * 100) if total_trades else 0.0, 4),
            "total_pnl_pct": round(total_pnl_pct, 4),
            "total_pnl_won": round(total_pnl_won, 0),
            "avg_pnl_pct": round(total_pnl_pct / total_trades if total_trades else 0.0, 4),
            "peak_ret_pct": round(peak, 4),
            "mdd_pct": round(max_drawdown, 4),
        }

    def flush(self) -> Optional[Path]:
        """현재 일일 요약을 파일로 저장."""
        self._sync_current_date()
        return self._flush_daily()

    def _flush_daily(self) -> Optional[Path]:
        """일일 요약 저장 후 trades 초기화."""
        if not self._trades:
            return None
        summary = self._build_daily_summary()
        path = self._data_dir / f"{summary.date}_summary.json"
        try:
            path.write_text(
                json.dumps(asdict(summary), ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            logger.info(
                "Performance summary saved: %s (%d trades, %.2f%% win rate, PF=%.2f)",
                path.name, summary.total_trades, summary.win_rate, summary.profit_factor,
            )
        except Exception:
            logger.exception("Failed to save performance summary")
            return None
        self._trades.clear()
        return path

    def _append_trade_log(self, record: TradeRecord) -> None:
        """개별 거래를 JSONL로 추가."""
        path = self._data_dir / f"{self._current_date}_trades.jsonl"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(record), ensure_ascii=False, default=str) + "\n")
        except Exception:
            logger.warning("Failed to append trade log to %s", path, exc_info=True)
