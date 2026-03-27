"""JSONL/JSON 로그 파일에서 대시보드 데이터를 로드하는 모듈."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

# 프로젝트 루트 기준 경로
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
RUNTIME_DIR = PROJECT_ROOT / "data" / "runtime"
REPLAY_DIR = PROJECT_ROOT / "data" / "replay" / "day_reports"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 파일을 읽어서 dict 리스트 반환."""
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def _read_json(path: Path) -> dict[str, Any] | None:
    """JSON 파일 읽기."""
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def available_dates() -> list[str]:
    """로그가 존재하는 날짜 목록 (YYYYMMDD) 반환, 최신순."""
    dates = set()
    for p in LOGS_DIR.glob("kindshot_*.jsonl"):
        stem = p.stem.replace("kindshot_", "")
        if len(stem) == 8 and stem.isdigit():
            dates.add(stem)
    return sorted(dates, reverse=True)


def load_events(date_str: str) -> pd.DataFrame:
    """특정 날짜의 이벤트 로그를 DataFrame으로 로드."""
    path = LOGS_DIR / f"kindshot_{date_str}.jsonl"
    records = _read_jsonl(path)
    events = [r for r in records if r.get("type") == "event"]
    if not events:
        return pd.DataFrame()
    df = pd.DataFrame(events)
    if "detected_at" in df.columns:
        df["detected_at"] = pd.to_datetime(df["detected_at"], errors="coerce")
    return df


def load_context_cards(date_str: str) -> pd.DataFrame:
    """특정 날짜의 context card(기술지표)를 DataFrame으로 로드."""
    path = RUNTIME_DIR / "context_cards" / f"{date_str}.jsonl"
    records = _read_jsonl(path)
    if not records:
        return pd.DataFrame()
    # ctx 필드를 풀어서 flat하게 만듦
    rows = []
    for r in records:
        ctx = r.get("ctx") or {}
        market = r.get("market_ctx") or {}
        row = {
            "event_id": r.get("event_id"),
            "ticker": r.get("ticker"),
            "corp_name": r.get("corp_name"),
            "bucket": r.get("bucket"),
            "rsi_14": ctx.get("rsi_14"),
            "macd_hist": ctx.get("macd_hist"),
            "bb_position": ctx.get("bb_position") or ctx.get("pos_20d"),
            "atr_14": ctx.get("atr_14"),
            "ret_today": ctx.get("ret_today"),
            "spread_bps": ctx.get("spread_bps"),
            "adv_value_20d": ctx.get("adv_value_20d"),
            "vol_pct_20d": ctx.get("vol_pct_20d"),
            "kospi_change_pct": market.get("kospi_change_pct"),
            "kosdaq_change_pct": market.get("kosdaq_change_pct"),
        }
        rows.append(row)
    return pd.DataFrame(rows)


def load_price_snapshots(date_str: str) -> pd.DataFrame:
    """특정 날짜의 가격 스냅샷을 DataFrame으로 로드."""
    path = RUNTIME_DIR / "price_snapshots" / f"{date_str}.jsonl"
    records = _read_jsonl(path)
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    return df


def load_replay_report(date_str: str) -> dict[str, Any] | None:
    """특정 날짜의 replay report 로드."""
    path = REPLAY_DIR / f"{date_str}.json"
    return _read_json(path)


def load_health() -> dict[str, Any] | None:
    """서버 health endpoint에서 데이터 로드 (로컬 서버 실행 중일 때)."""
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request("http://127.0.0.1:8080/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def load_guardrail_state() -> dict[str, Any] | None:
    """guardrail state JSON 로드 (파일 기반 fallback)."""
    # live state
    for sub in ["live", ""]:
        path = LOGS_DIR / "state" / sub / "guardrail_state.json" if sub else LOGS_DIR / "state" / "guardrail_state.json"
        data = _read_json(path)
        if data:
            return data
    return None


def compute_trade_pnl(date_str: str) -> pd.DataFrame:
    """이벤트 로그 + 가격 스냅샷으로 간이 PnL 계산.

    BUY 이벤트별 t0 → 각 horizon의 ret_long_vs_t0 추출.
    """
    events_df = load_events(date_str)
    snaps_df = load_price_snapshots(date_str)

    if events_df.empty or snaps_df.empty:
        return pd.DataFrame()

    # BUY 이벤트만
    buys = events_df[events_df["decision_action"] == "BUY"].copy()
    if buys.empty:
        return pd.DataFrame()

    rows = []
    for _, ev in buys.iterrows():
        eid = ev.get("event_id")
        ev_snaps = snaps_df[snaps_df["event_id"] == eid]
        if ev_snaps.empty:
            continue
        t0_row = ev_snaps[ev_snaps["horizon"] == "t0"]
        entry_px = t0_row.iloc[0]["px"] if not t0_row.empty and t0_row.iloc[0].get("px") else None

        best_ret = None
        final_ret = None
        final_horizon = None
        for _, snap in ev_snaps.iterrows():
            ret = snap.get("ret_long_vs_t0")
            if ret is not None:
                if best_ret is None or ret > best_ret:
                    best_ret = ret
                final_ret = ret
                final_horizon = snap.get("horizon")

        rows.append({
            "event_id": eid,
            "ticker": ev.get("ticker"),
            "corp_name": ev.get("corp_name"),
            "headline": ev.get("headline", "")[:60],
            "confidence": ev.get("decision_confidence"),
            "size_hint": ev.get("decision_size_hint"),
            "bucket": ev.get("bucket"),
            "entry_px": entry_px,
            "best_ret_pct": round(best_ret * 100, 2) if best_ret is not None else None,
            "final_ret_pct": round(final_ret * 100, 2) if final_ret is not None else None,
            "final_horizon": final_horizon,
        })

    return pd.DataFrame(rows)


def load_multi_day_events(n_days: int = 7) -> pd.DataFrame:
    """최근 n일의 이벤트를 합쳐서 반환."""
    dates = available_dates()[:n_days]
    frames = []
    for d in dates:
        df = load_events(d)
        if not df.empty:
            df["date"] = d
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
