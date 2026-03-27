"""종목별 학습 — 과거 트레이드 기반 종목 선호도 자동 조정."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TickerStats:
    """종목별 과거 트레이드 통계."""
    ticker: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float  # 0.0 ~ 1.0
    avg_pnl_pct: float
    total_pnl_pct: float


class TickerLearner:
    """과거 트레이드 데이터 기반 종목별 confidence 조정."""

    def __init__(self, min_trades: int = 3) -> None:
        self._stats: dict[str, TickerStats] = {}
        self._min_trades = min_trades

    def load_history(self, data_dir: Path) -> int:
        """performance JSONL 파일들에서 종목별 통계 로드.

        Returns: 로드된 트레이드 수
        """
        perf_dir = data_dir / "performance"
        if not perf_dir.exists():
            logger.info("No performance directory found at %s", perf_dir)
            return 0

        # 종목별 집계
        ticker_trades: dict[str, list[dict]] = {}
        total_loaded = 0

        for jsonl_file in sorted(perf_dir.glob("*_trades.jsonl")):
            try:
                for line in jsonl_file.read_text(encoding="utf-8").strip().split("\n"):
                    if not line.strip():
                        continue
                    try:
                        trade = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ticker = trade.get("ticker", "")
                    if not ticker:
                        continue
                    ticker_trades.setdefault(ticker, []).append(trade)
                    total_loaded += 1
            except Exception:
                logger.warning("Failed to read %s", jsonl_file)
                continue

        # 종목별 통계 계산
        for ticker, trades in ticker_trades.items():
            pnls = [t.get("pnl_pct", 0.0) for t in trades]
            wins = sum(1 for p in pnls if p > 0)
            losses = sum(1 for p in pnls if p <= 0)
            total = len(pnls)
            win_rate = wins / total if total > 0 else 0.0
            avg_pnl = sum(pnls) / total if total > 0 else 0.0
            total_pnl = sum(pnls)

            self._stats[ticker] = TickerStats(
                ticker=ticker,
                total_trades=total,
                wins=wins,
                losses=losses,
                win_rate=win_rate,
                avg_pnl_pct=round(avg_pnl, 2),
                total_pnl_pct=round(total_pnl, 2),
            )

        logger.info("TickerLearner loaded %d trades for %d tickers", total_loaded, len(self._stats))
        return total_loaded

    def get_stats(self, ticker: str) -> Optional[TickerStats]:
        """종목별 통계 조회."""
        return self._stats.get(ticker)

    def get_adjustment(self, ticker: str) -> int:
        """종목별 confidence 조정값 반환 (-5 ~ +5).

        조정 로직:
        - 거래 횟수 < min_trades → 0 (데이터 부족)
        - 승률 >= 70% → +5
        - 승률 >= 60% → +3
        - 승률 <= 20% → -5
        - 승률 <= 30% → -3
        - 그 외 → 0
        """
        stats = self._stats.get(ticker)
        if stats is None or stats.total_trades < self._min_trades:
            return 0

        if stats.win_rate >= 0.70:
            return 5
        if stats.win_rate >= 0.60:
            return 3
        if stats.win_rate <= 0.20:
            return -5
        if stats.win_rate <= 0.30:
            return -3
        return 0

    @property
    def ticker_count(self) -> int:
        return len(self._stats)

    @property
    def total_trades(self) -> int:
        return sum(s.total_trades for s in self._stats.values())
