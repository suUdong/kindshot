"""v86 universe ground truth — 현재 시점 pykrx 일봉 기준으로 각 ticker 의
breakout snapshot 을 계산해서 daemon 이 다음 scan 에서 어떤 시그널을 낼지 예측.

VolumeBreakoutFeed._qualifies 로직과 정확히 일치. universe 는 .env 의
VOLUME_BREAKOUT_FEED_TICKERS 사용.

출력: data/runtime/v86_diagnose_<TS>.csv + stderr 요약.
"""
from __future__ import annotations

import asyncio
import csv
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from kindshot.config import Config
from kindshot.feeds.volume_breakout_feed import _default_pykrx_breakout

RUNTIME = REPO / "data" / "runtime"


def evaluate(feats: dict, cfg: Config) -> tuple[bool, str, float | None]:
    """Replicate VolumeBreakoutFeed._qualifies. Return (qualifies, reject_reason, vol_ratio)."""
    if not feats:
        return False, "no_data", None
    close_today = feats.get("close_today") or 0.0
    prior_high_n = feats.get("prior_high_n")
    vol_today = feats.get("vol_today")
    prior_avg_vol_n = feats.get("prior_avg_vol_n")
    adv_value_n = feats.get("adv_value_n")
    ret_today = feats.get("ret_today")

    if prior_high_n is None or prior_avg_vol_n is None or adv_value_n is None:
        return False, "missing_features", None
    if vol_today is None or ret_today is None:
        return False, "missing_vol_or_ret", None
    if close_today <= 0 or prior_high_n <= 0 or prior_avg_vol_n <= 0:
        return False, "non_positive_features", None
    if adv_value_n < cfg.volume_breakout_feed_min_adv_value:
        return False, f"adv_too_low({adv_value_n:.0f} < {cfg.volume_breakout_feed_min_adv_value:.0f})", None
    if close_today <= prior_high_n:
        return False, f"no_breakout(close={close_today:.0f} <= prior_high={prior_high_n:.0f})", None
    vol_ratio = vol_today / prior_avg_vol_n
    if vol_ratio < cfg.volume_breakout_feed_min_vol_ratio:
        return False, f"vol_ratio_low({vol_ratio:.2f} < {cfg.volume_breakout_feed_min_vol_ratio})", vol_ratio
    if ret_today < cfg.volume_breakout_feed_min_ret_today:
        return False, f"ret_below_min({ret_today:.2f}% < {cfg.volume_breakout_feed_min_ret_today})", vol_ratio
    if ret_today > cfg.volume_breakout_feed_max_ret_today:
        return False, f"ret_above_max({ret_today:.2f}% > {cfg.volume_breakout_feed_max_ret_today})", vol_ratio
    return True, "qualifies", vol_ratio


async def main() -> int:
    cfg = Config()
    if not cfg.volume_breakout_feed_enabled:
        print("vb feed not enabled in config", file=sys.stderr)
        return 1

    tickers = list(cfg.volume_breakout_feed_tickers)
    lookback = cfg.volume_breakout_feed_lookback_n
    print(
        f"diagnose tickers={len(tickers)} lookback={lookback} min_vol_ratio={cfg.volume_breakout_feed_min_vol_ratio} "
        f"min_adv={cfg.volume_breakout_feed_min_adv_value:.0f}",
        file=sys.stderr,
    )

    RUNTIME.mkdir(parents=True, exist_ok=True)
    out = RUNTIME / f"v86_diagnose_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    cols = ["ticker", "close_today", "prior_high_n", "vol_today",
            "prior_avg_vol_n", "vol_ratio", "adv_value_n", "ret_today",
            "qualifies", "reject_reason"]

    qualifies_count = 0
    reject_buckets: dict[str, int] = {}
    rows: list[dict] = []

    for i, ticker in enumerate(tickers, 1):
        feats = await _default_pykrx_breakout(ticker, lookback)
        ok, reason, vol_ratio = evaluate(feats, cfg)
        bucket = reason.split("(")[0]
        reject_buckets[bucket] = reject_buckets.get(bucket, 0) + 1
        if ok:
            qualifies_count += 1
        row = {
            "ticker": ticker,
            "close_today": feats.get("close_today"),
            "prior_high_n": feats.get("prior_high_n"),
            "vol_today": feats.get("vol_today"),
            "prior_avg_vol_n": feats.get("prior_avg_vol_n"),
            "vol_ratio": round(vol_ratio, 3) if vol_ratio is not None else None,
            "adv_value_n": feats.get("adv_value_n"),
            "ret_today": feats.get("ret_today"),
            "qualifies": ok,
            "reject_reason": reason,
        }
        rows.append(row)
        print(f"[{i:2d}/{len(tickers)}] {ticker} qual={ok} reason={reason}", file=sys.stderr)

    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"\n=== summary ===", file=sys.stderr)
    print(f"qualifies={qualifies_count}/{len(tickers)}", file=sys.stderr)
    for bucket, cnt in sorted(reject_buckets.items(), key=lambda x: -x[1]):
        print(f"  {bucket}: {cnt}", file=sys.stderr)
    print(f"output={out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
