#!/usr/bin/env python3
"""v86 VolumeBreakoutFeed pykrx 백테스트.

신호 모델:
- close[t] > max(high[t-N..t-1])      (N-day high 돌파, 오늘 제외)
- vol[t]   >= K * mean(vol[t-N..t-1]) (거래량 폭증)
- ret_today (close[t]/close[t-1]-1)*100 ∈ [min_ret, max_ret]
- adv (mean(close*vol)[t-N..t-1]) >= min_adv

진입/청산 모델:
- 진입: 다음 거래일 시가 (open[t+1])
- 청산: t+1 부터 H 거래일 후 종가 (close[t+1+H-1])
- 거래비용: simple fee (buy 0.015%, sell 0.015%, tax 0.20%)

사용:
  python scripts/v86_backtest.py
  python scripts/v86_backtest.py --lookback-days 180 --hold-days 5 --vol-ratio 2.0

환경변수 캐시: pykrx 결과를 data/runtime/v86_backtest_cache/ 에 parquet 으로 저장.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
for p in (PROJECT_ROOT, SRC_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


DEFAULT_UNIVERSE = (
    # KOSPI 대형주 (시총 상위, 거래량 풍부)
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "035420",  # NAVER
    "035720",  # 카카오
    "005380",  # 현대차
    "051910",  # LG화학
    "006400",  # 삼성SDI
    "207940",  # 삼성바이오로직스
    "068270",  # 셀트리온
    "005490",  # POSCO홀딩스
    "000270",  # 기아
    "005935",  # 삼성전자우
    "066570",  # LG전자
    "003670",  # 포스코퓨처엠
    "012330",  # 현대모비스
    "028260",  # 삼성물산
    "017670",  # SK텔레콤
    "030200",  # KT
    "015760",  # 한국전력
    "055550",  # 신한지주
    "105560",  # KB금융
    "086790",  # 하나금융지주
    "316140",  # 우리금융지주
    "032830",  # 삼성생명
    "009150",  # 삼성전기
    "036460",  # 한국가스공사
    "010130",  # 고려아연
    "010950",  # S-Oil
    "096770",  # SK이노베이션
    "034730",  # SK
    # KOSDAQ 대형주
    "247540",  # 에코프로비엠
    "086520",  # 에코프로
    "091990",  # 셀트리온헬스케어
    "196170",  # 알테오젠
    "112040",  # 위메이드
    "041510",  # SM
    "035900",  # JYP Ent.
    "067310",  # 하나마이크론
    "058470",  # 리노공업
    "293490",  # 카카오게임즈
)


@dataclass
class TradeResult:
    ticker: str
    entry_date: str
    exit_date: str
    entry_px: float
    exit_px: float
    raw_ret_pct: float
    net_ret_pct: float
    vol_ratio: float
    breakout_pct: float
    ret_today_at_signal: float


@dataclass
class BacktestStats:
    n_trades: int
    n_winners: int
    winrate_pct: float
    avg_raw_ret_pct: float
    avg_net_ret_pct: float
    median_net_ret_pct: float
    best_net_ret_pct: float
    worst_net_ret_pct: float
    n_tickers_with_trades: int


def _fetch_ohlcv(ticker: str, start: str, end: str, cache_dir: Path):
    """pykrx 일봉 fetch (parquet 캐시)."""
    import pandas as pd
    from pykrx import stock

    cache_path = cache_dir / f"{ticker}_{start}_{end}.parquet"
    if cache_path.exists():
        try:
            return pd.read_parquet(cache_path)
        except Exception:
            cache_path.unlink(missing_ok=True)

    for attempt in range(3):
        try:
            df = stock.get_market_ohlcv(start, end, ticker)
            if df is None or df.empty:
                return None
            cache_dir.mkdir(parents=True, exist_ok=True)
            try:
                df.to_parquet(cache_path)
            except Exception:
                pass
            return df
        except Exception as exc:
            if attempt == 2:
                print(f"[warn] fetch failed for {ticker}: {exc}", file=sys.stderr)
                return None
            time.sleep(1.5)
    return None


def _column(df, kor: str, eng: str) -> str:
    if kor in df.columns:
        return kor
    if eng in df.columns:
        return eng
    raise KeyError(f"neither {kor!r} nor {eng!r} in columns {list(df.columns)}")


def _evaluate(
    df,
    *,
    lookback_n: int,
    min_vol_ratio: float,
    min_ret_today: float,
    max_ret_today: float,
    min_adv: float,
    hold_days: int,
    fee_buy_pct: float,
    fee_sell_pct: float,
    tax_sell_pct: float,
    cooldown_days: int,
) -> list[TradeResult]:
    close_col = _column(df, "종가", "Close")
    high_col = _column(df, "고가", "High")
    open_col = _column(df, "시가", "Open")
    vol_col = _column(df, "거래량", "Volume")

    close = df[close_col].astype(float).values
    high = df[high_col].astype(float).values
    open_ = df[open_col].astype(float).values
    vol = df[vol_col].astype(float).values
    dates = list(df.index)

    n = len(close)
    if n < lookback_n + hold_days + 2:
        return []

    trades: list[TradeResult] = []
    last_signal_idx = -10_000

    # t 는 신호 발생일 (그날 종가까지 알 수 있음), 진입은 t+1 시가
    for t in range(lookback_n + 1, n - hold_days - 1):
        if t - last_signal_idx < cooldown_days:
            continue

        prior_high = float(max(high[t - lookback_n:t]))
        prior_avg_vol = float(sum(vol[t - lookback_n:t]) / lookback_n)
        prior_adv = float(sum(close[t - lookback_n:t] * vol[t - lookback_n:t]) / lookback_n)

        close_today = float(close[t])
        prev_close = float(close[t - 1])
        if prev_close <= 0 or prior_avg_vol <= 0 or prior_high <= 0:
            continue

        ret_today = (close_today / prev_close - 1) * 100
        vol_ratio = float(vol[t]) / prior_avg_vol
        breakout_pct = (close_today / prior_high - 1) * 100

        if close_today <= prior_high:
            continue
        if vol_ratio < min_vol_ratio:
            continue
        if ret_today < min_ret_today or ret_today > max_ret_today:
            continue
        if prior_adv < min_adv:
            continue

        entry_idx = t + 1
        exit_idx = t + hold_days
        if exit_idx >= n:
            continue

        entry_px = float(open_[entry_idx])
        exit_px = float(close[exit_idx])
        if entry_px <= 0:
            continue

        raw_ret_pct = (exit_px / entry_px - 1) * 100
        cost_pct = fee_buy_pct + fee_sell_pct + tax_sell_pct
        net_ret_pct = raw_ret_pct - cost_pct

        last_signal_idx = t
        trades.append(TradeResult(
            ticker="",  # filled by caller
            entry_date=str(dates[entry_idx])[:10],
            exit_date=str(dates[exit_idx])[:10],
            entry_px=entry_px,
            exit_px=exit_px,
            raw_ret_pct=round(raw_ret_pct, 3),
            net_ret_pct=round(net_ret_pct, 3),
            vol_ratio=round(vol_ratio, 3),
            breakout_pct=round(breakout_pct, 3),
            ret_today_at_signal=round(ret_today, 3),
        ))
    return trades


def _aggregate(all_trades: list[TradeResult]) -> BacktestStats:
    if not all_trades:
        return BacktestStats(
            n_trades=0,
            n_winners=0,
            winrate_pct=0.0,
            avg_raw_ret_pct=0.0,
            avg_net_ret_pct=0.0,
            median_net_ret_pct=0.0,
            best_net_ret_pct=0.0,
            worst_net_ret_pct=0.0,
            n_tickers_with_trades=0,
        )
    net_rets = sorted(t.net_ret_pct for t in all_trades)
    n = len(net_rets)
    median = net_rets[n // 2] if n % 2 == 1 else (net_rets[n // 2 - 1] + net_rets[n // 2]) / 2
    winners = sum(1 for t in all_trades if t.net_ret_pct > 0)
    return BacktestStats(
        n_trades=n,
        n_winners=winners,
        winrate_pct=round(winners / n * 100, 2),
        avg_raw_ret_pct=round(sum(t.raw_ret_pct for t in all_trades) / n, 3),
        avg_net_ret_pct=round(sum(t.net_ret_pct for t in all_trades) / n, 3),
        median_net_ret_pct=round(median, 3),
        best_net_ret_pct=max(net_rets),
        worst_net_ret_pct=min(net_rets),
        n_tickers_with_trades=len({t.ticker for t in all_trades}),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="v86 VolumeBreakoutFeed backtest")
    parser.add_argument("--lookback-days", type=int, default=180,
                        help="과거 거래일 데이터 길이 (default 180)")
    parser.add_argument("--lookback-n", type=int, default=20,
                        help="N-day high 윈도우 (default 20)")
    parser.add_argument("--vol-ratio", type=float, default=2.0)
    parser.add_argument("--min-ret-today", type=float, default=0.0)
    parser.add_argument("--max-ret-today", type=float, default=7.0)
    parser.add_argument("--min-adv", type=float, default=500_000_000.0)
    parser.add_argument("--hold-days", type=int, default=5)
    parser.add_argument("--cooldown-days", type=int, default=5,
                        help="신호 후 N 거래일 cooldown")
    parser.add_argument("--fee-buy-pct", type=float, default=0.015)
    parser.add_argument("--fee-sell-pct", type=float, default=0.015)
    parser.add_argument("--tax-sell-pct", type=float, default=0.20)
    parser.add_argument("--universe", type=str, default="",
                        help="CSV ticker list (default = DEFAULT_UNIVERSE)")
    parser.add_argument("--cache-dir", type=str, default="data/runtime/v86_backtest_cache")
    parser.add_argument("--output-json", type=str, default="")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    universe = (
        tuple(t.strip() for t in args.universe.split(",") if t.strip())
        if args.universe
        else DEFAULT_UNIVERSE
    )

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=args.lookback_days + args.lookback_n * 2 + 30)
    start = start_dt.strftime("%Y%m%d")
    end = end_dt.strftime("%Y%m%d")
    cache_dir = Path(args.cache_dir)

    print(f"[v86 backtest] universe={len(universe)} tickers, period={start}..{end}, "
          f"lookback_n={args.lookback_n}, vol_ratio>={args.vol_ratio}x, hold={args.hold_days}d, "
          f"cooldown={args.cooldown_days}d")

    all_trades: list[TradeResult] = []
    n_no_data = 0
    for i, ticker in enumerate(universe, 1):
        df = _fetch_ohlcv(ticker, start, end, cache_dir)
        if df is None or df.empty:
            n_no_data += 1
            if args.verbose:
                print(f"[{i}/{len(universe)}] {ticker}: NO DATA")
            continue

        trades = _evaluate(
            df,
            lookback_n=args.lookback_n,
            min_vol_ratio=args.vol_ratio,
            min_ret_today=args.min_ret_today,
            max_ret_today=args.max_ret_today,
            min_adv=args.min_adv,
            hold_days=args.hold_days,
            fee_buy_pct=args.fee_buy_pct,
            fee_sell_pct=args.fee_sell_pct,
            tax_sell_pct=args.tax_sell_pct,
            cooldown_days=args.cooldown_days,
        )
        for tr in trades:
            tr.ticker = ticker
        all_trades.extend(trades)
        if args.verbose:
            print(f"[{i}/{len(universe)}] {ticker}: {len(trades)} signals")

    stats = _aggregate(all_trades)
    print()
    print("=== v86 Backtest Result ===")
    print(f"Tickers fetched: {len(universe) - n_no_data}/{len(universe)}  (no_data={n_no_data})")
    print(f"Trades        : {stats.n_trades}")
    print(f"Winrate       : {stats.winrate_pct:.2f}%  ({stats.n_winners}/{stats.n_trades})")
    print(f"Avg raw ret   : {stats.avg_raw_ret_pct:+.3f}%")
    print(f"Avg net ret   : {stats.avg_net_ret_pct:+.3f}%")
    print(f"Median net    : {stats.median_net_ret_pct:+.3f}%")
    print(f"Best net      : {stats.best_net_ret_pct:+.3f}%")
    print(f"Worst net     : {stats.worst_net_ret_pct:+.3f}%")
    print(f"Tickers traded: {stats.n_tickers_with_trades}")

    if args.output_json:
        out = {
            "params": vars(args),
            "stats": asdict(stats),
            "trades": [asdict(t) for t in all_trades],
            "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        }
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"\nSaved to {args.output_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
