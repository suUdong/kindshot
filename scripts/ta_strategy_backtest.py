#!/usr/bin/env python3
"""Technical Analysis Strategy Backtest for KOSPI large-cap stocks.

Uses pykrx to fetch 3-month daily OHLCV data and backtests 4 strategies:
1. Momentum (N-day return)
2. Gap Breakout (open gap-up/gap-down)
3. Mean Reversion (oversold bounce)
4. Volume Spike (abnormal volume detection)

Outputs: strategy comparison (win rate, Sharpe, MDD) and markdown report.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pykrx import stock

# ── KOSPI 대형주 100종목 (시가총액 상위, 2024 기준) ──────────────────
# pykrx ticker_list API 불안정 → 하드코딩
KOSPI_LARGE_CAPS: dict[str, str] = {
    "005930": "삼성전자", "000660": "SK하이닉스", "373220": "LG에너지솔루션",
    "207940": "삼성바이오로직스", "005380": "현대차", "006400": "삼성SDI",
    "035420": "NAVER", "000270": "기아", "068270": "셀트리온",
    "051910": "LG화학", "105560": "KB금융", "055550": "신한지주",
    "035720": "카카오", "012330": "현대모비스", "003670": "포스코퓨처엠",
    "028260": "삼성물산", "066570": "LG전자", "096770": "SK이노베이션",
    "086790": "하나금융지주", "034730": "SK", "015760": "한국전력",
    "003550": "LG", "032830": "삼성생명", "259960": "크래프톤",
    "138040": "메리츠금융지주", "009150": "삼성전기", "018260": "삼성에스디에스",
    "010130": "고려아연", "033780": "KT&G", "000810": "삼성화재",
    "316140": "우리금융지주", "011200": "HMM", "329180": "현대오토에버",
    "017670": "SK텔레콤", "024110": "기업은행", "003490": "대한항공",
    "034020": "두산에너빌리티", "010950": "S-Oil", "009540": "한국조선해양",
    "036570": "엔씨소프트", "030200": "KT", "090430": "아모레퍼시픽",
    "011170": "롯데케미칼", "005490": "POSCO홀딩스", "000720": "현대건설",
    "161390": "한국타이어앤테크놀로지", "006800": "미래에셋증권",
    "047050": "포스코인터내셔널", "010140": "삼성중공업", "004020": "현대제철",
    "267250": "HD현대", "001450": "현대해상", "078930": "GS",
    "326030": "SK바이오팜", "021240": "코웨이", "180640": "한진칼",
    "323410": "카카오뱅크", "352820": "하이브", "251270": "넷마블",
    "011790": "SKC", "009830": "한화솔루션", "016360": "삼성증권",
    "402340": "SK스퀘어", "006360": "GS건설", "088980": "맥쿼리인프라",
    "071050": "한국금융지주", "128940": "한미약품", "004990": "롯데지주",
    "139480": "이마트", "097950": "CJ제일제당", "005830": "DB손해보험",
    "002790": "아모레G", "000100": "유한양행", "307950": "현대오일뱅크",
    "003410": "쌍용C&E", "069500": "KODEX200",  # ETF 참고용
}

# ── 설정 ────────────────────────────────────────────────────────────
DATA_START = "20240101"
DATA_END = "20240329"
HOLD_DAYS = 5          # 포지션 보유일
COMMISSION_BPS = 15    # 편도 수수료 (세금 포함)
FETCH_DELAY = 0.3      # pykrx rate limit


@dataclass
class Trade:
    ticker: str
    name: str
    strategy: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    ret_pct: float  # 수수료 차감 후


@dataclass
class StrategyResult:
    name: str
    trades: list[Trade] = field(default_factory=list)
    total_trades: int = 0
    win_rate: float = 0.0
    avg_return: float = 0.0
    sharpe: float = 0.0
    max_dd: float = 0.0
    cumulative_return: float = 0.0

    def compute(self):
        if not self.trades:
            return
        rets = [t.ret_pct for t in self.trades]
        self.total_trades = len(rets)
        self.win_rate = sum(1 for r in rets if r > 0) / len(rets) * 100
        self.avg_return = np.mean(rets)
        self.sharpe = (np.mean(rets) / np.std(rets) * np.sqrt(252 / HOLD_DAYS)
                       if np.std(rets) > 0 else 0)
        # MDD from cumulative equity curve
        equity = np.cumprod(1 + np.array(rets) / 100)
        peak = np.maximum.accumulate(equity)
        dd = (equity - peak) / peak * 100
        self.max_dd = dd.min()
        self.cumulative_return = (equity[-1] - 1) * 100


# ── 데이터 수집 ─────────────────────────────────────────────────────
def fetch_all_data() -> dict[str, pd.DataFrame]:
    """KOSPI 대형주 OHLCV 수집."""
    data: dict[str, pd.DataFrame] = {}
    total = len(KOSPI_LARGE_CAPS)
    for i, (ticker, name) in enumerate(KOSPI_LARGE_CAPS.items()):
        try:
            df = stock.get_market_ohlcv_by_date(DATA_START, DATA_END, ticker)
            if df.empty or len(df) < 20:
                continue
            df.columns = ["open", "high", "low", "close", "volume", "change_pct"]
            data[ticker] = df
            if (i + 1) % 20 == 0:
                print(f"  [{i+1}/{total}] 수집 완료...")
            time.sleep(FETCH_DELAY)
        except Exception as e:
            print(f"  SKIP {ticker} ({name}): {e}")
            time.sleep(1)
    return data


# ── 전략 구현 ───────────────────────────────────────────────────────

def strategy_momentum(data: dict[str, pd.DataFrame], lookback: int = 20) -> list[Trade]:
    """모멘텀: N일 수익률 상위 종목 매수 → HOLD_DAYS 후 청산."""
    trades: list[Trade] = []
    # 모든 종목의 N일 수익률 계산
    all_tickers = list(data.keys())
    if not all_tickers:
        return trades
    ref_dates = data[all_tickers[0]].index

    for idx in range(lookback, len(ref_dates) - HOLD_DAYS, HOLD_DAYS):
        date = ref_dates[idx]
        # 각 종목 lookback 수익률
        scores = {}
        for ticker, df in data.items():
            if date not in df.index:
                continue
            loc = df.index.get_loc(date)
            if loc < lookback:
                continue
            ret = (df.iloc[loc]["close"] / df.iloc[loc - lookback]["close"] - 1) * 100
            scores[ticker] = ret

        if not scores:
            continue
        # 상위 10% 매수
        sorted_tickers = sorted(scores, key=scores.get, reverse=True)
        top_n = max(1, len(sorted_tickers) // 10)
        for ticker in sorted_tickers[:top_n]:
            df = data[ticker]
            if date not in df.index:
                continue
            loc = df.index.get_loc(date)
            exit_loc = min(loc + HOLD_DAYS, len(df) - 1)
            entry_p = df.iloc[loc]["close"]
            exit_p = df.iloc[exit_loc]["close"]
            ret = (exit_p / entry_p - 1) * 100 - (COMMISSION_BPS * 2 / 100)
            trades.append(Trade(
                ticker=ticker, name=KOSPI_LARGE_CAPS.get(ticker, ""),
                strategy="momentum", entry_date=str(date.date()),
                entry_price=entry_p, exit_date=str(df.index[exit_loc].date()),
                exit_price=exit_p, ret_pct=round(ret, 2),
            ))
    return trades


def strategy_gap_breakout(data: dict[str, pd.DataFrame], gap_threshold: float = 2.0) -> list[Trade]:
    """갭 브레이크아웃: 시가가 전일 종가 대비 gap_threshold% 이상 갭업 → 당일 시가 매수, HOLD_DAYS 후 청산."""
    trades: list[Trade] = []
    for ticker, df in data.items():
        for i in range(1, len(df) - HOLD_DAYS):
            prev_close = df.iloc[i - 1]["close"]
            today_open = df.iloc[i]["open"]
            gap_pct = (today_open / prev_close - 1) * 100

            if gap_pct >= gap_threshold:
                entry_p = today_open
                exit_loc = min(i + HOLD_DAYS, len(df) - 1)
                exit_p = df.iloc[exit_loc]["close"]
                ret = (exit_p / entry_p - 1) * 100 - (COMMISSION_BPS * 2 / 100)
                trades.append(Trade(
                    ticker=ticker, name=KOSPI_LARGE_CAPS.get(ticker, ""),
                    strategy="gap_breakout", entry_date=str(df.index[i].date()),
                    entry_price=entry_p, exit_date=str(df.index[exit_loc].date()),
                    exit_price=exit_p, ret_pct=round(ret, 2),
                ))
    return trades


def strategy_mean_reversion(data: dict[str, pd.DataFrame],
                             lookback: int = 20, z_threshold: float = -2.0) -> list[Trade]:
    """평균회귀: 종가가 20일 이동평균 대비 z-score가 임계값 이하 → 매수, HOLD_DAYS 후 청산."""
    trades: list[Trade] = []
    for ticker, df in data.items():
        if len(df) < lookback + HOLD_DAYS:
            continue
        ma = df["close"].rolling(lookback).mean()
        std = df["close"].rolling(lookback).std()
        z = (df["close"] - ma) / std

        for i in range(lookback, len(df) - HOLD_DAYS):
            if z.iloc[i] <= z_threshold:
                entry_p = df.iloc[i]["close"]
                exit_loc = min(i + HOLD_DAYS, len(df) - 1)
                exit_p = df.iloc[exit_loc]["close"]
                ret = (exit_p / entry_p - 1) * 100 - (COMMISSION_BPS * 2 / 100)
                trades.append(Trade(
                    ticker=ticker, name=KOSPI_LARGE_CAPS.get(ticker, ""),
                    strategy="mean_reversion", entry_date=str(df.index[i].date()),
                    entry_price=entry_p, exit_date=str(df.index[exit_loc].date()),
                    exit_price=exit_p, ret_pct=round(ret, 2),
                ))
    return trades


def strategy_volume_spike(data: dict[str, pd.DataFrame],
                           lookback: int = 20, spike_mult: float = 3.0) -> list[Trade]:
    """거래량 스파이크: 거래량이 20일 평균의 spike_mult배 이상 + 양봉 → 매수, HOLD_DAYS 후 청산."""
    trades: list[Trade] = []
    for ticker, df in data.items():
        if len(df) < lookback + HOLD_DAYS:
            continue
        vol_ma = df["volume"].rolling(lookback).mean()

        for i in range(lookback, len(df) - HOLD_DAYS):
            vol_ratio = df.iloc[i]["volume"] / vol_ma.iloc[i] if vol_ma.iloc[i] > 0 else 0
            is_bullish = df.iloc[i]["close"] > df.iloc[i]["open"]

            if vol_ratio >= spike_mult and is_bullish:
                entry_p = df.iloc[i]["close"]
                exit_loc = min(i + HOLD_DAYS, len(df) - 1)
                exit_p = df.iloc[exit_loc]["close"]
                ret = (exit_p / entry_p - 1) * 100 - (COMMISSION_BPS * 2 / 100)
                trades.append(Trade(
                    ticker=ticker, name=KOSPI_LARGE_CAPS.get(ticker, ""),
                    strategy="volume_spike", entry_date=str(df.index[i].date()),
                    entry_price=entry_p, exit_date=str(df.index[exit_loc].date()),
                    exit_price=exit_p, ret_pct=round(ret, 2),
                ))
    return trades


# ── 리포트 생성 ─────────────────────────────────────────────────────

def generate_report(results: list[StrategyResult], data_count: int) -> str:
    """마크다운 리포트 생성."""
    lines = [
        "# 기술적 분석 전략 백테스트 결과",
        "",
        f"- **기간**: {DATA_START[:4]}-{DATA_START[4:6]}-{DATA_START[6:]} ~ "
        f"{DATA_END[:4]}-{DATA_END[4:6]}-{DATA_END[6:]}",
        f"- **종목 수**: KOSPI 대형주 {data_count}개",
        f"- **보유일**: {HOLD_DAYS}일",
        f"- **수수료**: 편도 {COMMISSION_BPS}bps (왕복 {COMMISSION_BPS*2}bps)",
        "",
        "## 전략별 성과 비교",
        "",
        "| 전략 | 거래 수 | 승률 | 평균수익 | Sharpe | MDD | 누적수익 |",
        "|------|---------|------|----------|--------|-----|----------|",
    ]
    for r in results:
        lines.append(
            f"| {r.name} | {r.total_trades} | {r.win_rate:.1f}% | "
            f"{r.avg_return:.2f}% | {r.sharpe:.2f} | {r.max_dd:.1f}% | "
            f"{r.cumulative_return:.1f}% |"
        )

    # 전략 상세 설명
    lines += [
        "",
        "## 전략 상세",
        "",
        "### 1. 모멘텀 (Momentum)",
        "- **로직**: 20일 수익률 상위 10% 종목 매수 → 5일 보유",
        "- **근거**: 강한 추세를 보이는 종목이 단기적으로 추가 상승하는 경향",
        "- **한국 시장 특성**: 외국인/기관 수급이 몰리는 종목에서 모멘텀 효과 강함",
        "",
        "### 2. 갭 브레이크아웃 (Gap Breakout)",
        "- **로직**: 전일 종가 대비 2%+ 갭업으로 시작하는 종목 매수 → 5일 보유",
        "- **근거**: 갭업은 강한 매수세 유입 신호, 후속 상승 가능성",
        "- **리스크**: 고점 추격 위험, 갭 메우기(gap fill) 패턴",
        "",
        "### 3. 평균회귀 (Mean Reversion)",
        "- **로직**: 20일 이동평균 대비 z-score ≤ -2.0인 종목 매수 → 5일 보유",
        "- **근거**: 과매도 종목의 단기 반등 기대",
        "- **한국 시장 특성**: 대형주는 평균회귀 경향 강함 (기관 바구니매매)",
        "",
        "### 4. 거래량 스파이크 (Volume Spike)",
        "- **로직**: 거래량이 20일 평균의 3배+ 이면서 양봉인 종목 매수 → 5일 보유",
        "- **근거**: 이상 거래량 + 양봉 = 세력/기관 매집 신호",
        "- **장점**: 공시 기반 kindshot과 시너지 가능 (뉴스 + 거래량 확인)",
        "",
    ]

    # 추천
    sorted_results = sorted(results, key=lambda r: r.sharpe, reverse=True)
    top = sorted_results[:2]
    lines += [
        "## 추천 전략",
        "",
    ]
    for i, r in enumerate(top, 1):
        lines.append(f"### {i}순위: {r.name}")
        lines.append(f"- Sharpe {r.sharpe:.2f}, 승률 {r.win_rate:.1f}%, MDD {r.max_dd:.1f}%")
        lines.append("")

    lines += [
        "## kindshot 통합 방안",
        "",
        "1. **거래량 스파이크 + 공시**: 뉴스 시그널 발생 시 거래량 확인으로 신뢰도 boost",
        "2. **모멘텀 필터**: 진입 전 N일 모멘텀 체크 → 하락 추세 종목 필터링",
        "3. **평균회귀 역이용**: 이미 과매수(z-score > 2) 종목 진입 회피",
        "",
        "## 다음 단계",
        "",
        "1. 분봉 데이터로 인트라데이 전략 백테스트",
        "2. 파라미터 최적화 (lookback, threshold, hold days)",
        "3. kindshot 파이프라인에 TA 필터 모듈 추가",
        "4. 복합 전략 (공시 + TA 시그널 결합) 테스트",
    ]
    return "\n".join(lines)


# ── 메인 ────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("KOSPI 대형주 기술적 분석 전략 백테스트")
    print(f"기간: {DATA_START} ~ {DATA_END}")
    print("=" * 60)

    # 1) 데이터 수집
    print("\n[1/3] 데이터 수집 중...")
    data = fetch_all_data()
    print(f"  → {len(data)}개 종목 수집 완료")

    if not data:
        print("ERROR: 데이터 수집 실패")
        sys.exit(1)

    # 2) 전략 실행
    print("\n[2/3] 전략 백테스트 실행...")

    strategies = {
        "momentum": strategy_momentum(data),
        "gap_breakout": strategy_gap_breakout(data),
        "mean_reversion": strategy_mean_reversion(data),
        "volume_spike": strategy_volume_spike(data),
    }

    results: list[StrategyResult] = []
    for name, trades in strategies.items():
        r = StrategyResult(name=name, trades=trades)
        r.compute()
        results.append(r)
        print(f"  {name:20s}: {r.total_trades:4d} trades, "
              f"WR {r.win_rate:5.1f}%, Sharpe {r.sharpe:6.2f}, "
              f"MDD {r.max_dd:6.1f}%, Cum {r.cumulative_return:6.1f}%")

    # 3) 리포트 작성
    print("\n[3/3] 리포트 생성...")
    report = generate_report(results, len(data))
    report_path = Path(__file__).resolve().parent.parent / "reports" / "technical-strategy-research.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"  → {report_path}")

    # JSON 결과도 저장
    json_path = report_path.with_suffix(".json")
    json_data = {
        "period": {"start": DATA_START, "end": DATA_END},
        "stocks_count": len(data),
        "hold_days": HOLD_DAYS,
        "commission_bps": COMMISSION_BPS,
        "strategies": {},
    }
    for r in results:
        json_data["strategies"][r.name] = {
            "total_trades": r.total_trades,
            "win_rate": round(r.win_rate, 1),
            "avg_return": round(r.avg_return, 2),
            "sharpe": round(r.sharpe, 2),
            "max_dd": round(r.max_dd, 1),
            "cumulative_return": round(r.cumulative_return, 1),
            "top_trades": [
                {"ticker": t.ticker, "name": t.name, "entry": t.entry_date,
                 "exit": t.exit_date, "ret": t.ret_pct}
                for t in sorted(r.trades, key=lambda x: x.ret_pct, reverse=True)[:10]
            ],
        }
    json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → {json_path}")

    print("\n✓ 완료")


if __name__ == "__main__":
    main()
