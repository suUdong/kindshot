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
    size_won: float = 0.0
    pnl_won: float = 0.0
    hold_seconds: int = 0
    exit_type: str = ""  # TP, SL, TRAILING, TIMEOUT, MANUAL
    confidence: int = 0
    bucket: str = ""
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

    def record_trade(
        self,
        ticker: str,
        entry_px: float,
        exit_px: float,
        pnl_pct: float,
        *,
        size_won: float = 0.0,
        hold_seconds: int = 0,
        exit_type: str = "",
        confidence: int = 0,
        bucket: str = "",
    ) -> TradeRecord:
        """거래 결과 기록."""
        today = datetime.now(_KST).strftime("%Y-%m-%d")
        if today != self._current_date:
            self._flush_daily()
            self._current_date = today

        pnl_won = size_won * (pnl_pct / 100) if size_won > 0 else 0.0
        record = TradeRecord(
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
            timestamp=datetime.now(_KST).isoformat(),
        )
        self._trades.append(record)

        # Append to daily JSONL immediately
        self._append_trade_log(record)
        return record

    def daily_summary(self) -> DailySummary:
        """현재 일일 요약 계산."""
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

    def flush(self) -> Optional[Path]:
        """현재 일일 요약을 파일로 저장."""
        return self._flush_daily()

    def _flush_daily(self) -> Optional[Path]:
        """일일 요약 저장 후 trades 초기화."""
        if not self._trades:
            return None
        summary = self.daily_summary()
        path = self._data_dir / f"{self._current_date}_summary.json"
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
            logger.debug("Failed to append trade log")
