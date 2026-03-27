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


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _with_equity_and_drawdown(
    df: pd.DataFrame,
    *,
    pnl_col: str,
    order_col: str,
) -> pd.DataFrame:
    if df.empty or pnl_col not in df.columns:
        return df
    ordered = df.sort_values(order_col, kind="stable").copy()
    ordered[pnl_col] = pd.to_numeric(ordered[pnl_col], errors="coerce").fillna(0.0)
    ordered["cum_ret_pct"] = ordered[pnl_col].cumsum()
    ordered["peak_ret_pct"] = ordered["cum_ret_pct"].cummax()
    ordered["drawdown_pct"] = ordered["cum_ret_pct"] - ordered["peak_ret_pct"]
    return ordered


def available_dates() -> list[str]:
    """로그가 존재하는 날짜 목록 (YYYYMMDD) 반환, 최신순."""
    dates = set()
    for p in LOGS_DIR.glob("kindshot_*.jsonl"):
        stem = p.stem.replace("kindshot_", "")
        if len(stem) == 8 and stem.isdigit():
            dates.add(stem)
    return sorted(dates, reverse=True)


def load_events(date_str: str) -> pd.DataFrame:
    """특정 날짜의 이벤트 로그를 DataFrame으로 로드.

    decision 레코드의 decision_source, llm_model, llm_latency_ms를 이벤트에 조인.
    """
    path = LOGS_DIR / f"kindshot_{date_str}.jsonl"
    records = _read_jsonl(path)
    events = [r for r in records if r.get("type") == "event"]
    decisions = {
        r["event_id"]: r for r in records
        if r.get("type") == "decision" and r.get("event_id")
    }
    if not events:
        return pd.DataFrame()
    # decision 필드를 event에 조인
    for ev in events:
        dec = decisions.get(ev.get("event_id"))
        if dec:
            ev.setdefault("decision_source", dec.get("decision_source"))
            ev.setdefault("llm_model", dec.get("llm_model"))
            ev.setdefault("llm_latency_ms", dec.get("llm_latency_ms"))
    df = pd.DataFrame(events)
    if "detected_at" in df.columns:
        df["detected_at"] = pd.to_datetime(df["detected_at"], errors="coerce")
    # guardrail 차단된 BUY는 실제 매매 아님 → 구분 컬럼 추가
    if "decision_action" in df.columns and "guardrail_result" in df.columns:
        df["effective_action"] = df.apply(
            lambda r: "GUARDRAIL_BLOCKED" if r.get("decision_action") == "BUY"
            and r.get("guardrail_result") not in (None, "", "PASS")
            and r.get("skip_stage") == "GUARDRAIL"
            else r.get("decision_action"),
            axis=1,
        )
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
            "detected_at": ev.get("detected_at"),
            "guardrail_result": ev.get("guardrail_result"),
            "entry_px": entry_px,
            "best_ret_pct": round(best_ret * 100, 2) if best_ret is not None else None,
            "final_ret_pct": round(final_ret * 100, 2) if final_ret is not None else None,
            "final_horizon": final_horizon,
        })

    return pd.DataFrame(rows)


def compute_daily_equity_curve(date_str: str) -> pd.DataFrame:
    """당일 실행 BUY 기준 누적 수익곡선 + 드로다운."""
    pnl_df = compute_trade_pnl(date_str)
    if pnl_df.empty:
        return pd.DataFrame()
    valid = pnl_df.dropna(subset=["final_ret_pct"]).copy()
    if valid.empty:
        return pd.DataFrame()
    if "detected_at" in valid.columns:
        valid["detected_at"] = pd.to_datetime(valid["detected_at"], errors="coerce")
    valid["trade_label"] = valid.apply(
        lambda row: f"{row.get('ticker', '')} {str(row.get('headline', ''))[:24]}".strip(),
        axis=1,
    )
    return _with_equity_and_drawdown(valid, pnl_col="final_ret_pct", order_col="detected_at")


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


def load_multi_day_pnl_detail(n_days: int = 7) -> pd.DataFrame:
    """최근 n일의 개별 매매 PnL을 합쳐서 반환 (키워드/버킷/시간대 분석용)."""
    dates = sorted(available_dates()[:n_days])
    frames = []
    for d in dates:
        ev_df = load_events(d)
        pnl = compute_trade_pnl(d)
        if pnl.empty or ev_df.empty:
            continue
        # keyword_hits, detected_at 조인
        join_cols = ["event_id"]
        extra = []
        for col in ["keyword_hits", "detected_at", "decision_source"]:
            if col in ev_df.columns:
                extra.append(col)
        if extra:
            merged = pnl.merge(ev_df[join_cols + extra], on="event_id", how="left")
        else:
            merged = pnl
        merged["date"] = d
        frames.append(merged)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def compute_multi_day_pnl(n_days: int = 7) -> pd.DataFrame:
    """최근 n일의 일별 PnL 요약 (누적 수익률 포함)."""
    dates = sorted(available_dates()[:n_days])
    rows = []
    cum_pnl = 0.0
    for d in dates:
        pnl = compute_trade_pnl(d)
        if pnl.empty:
            rows.append({
                "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                "trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0.0, "avg_ret_pct": 0.0,
                "total_ret_pct": 0.0, "cum_ret_pct": cum_pnl,
            })
            continue
        valid = pnl["final_ret_pct"].dropna()
        wins = int((valid > 0).sum())
        losses = int((valid <= 0).sum())
        n_trades = wins + losses
        day_total = float(valid.sum()) if len(valid) else 0.0
        cum_pnl += day_total
        rows.append({
            "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
            "trades": n_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / n_trades * 100 if n_trades else 0.0,
            "avg_ret_pct": float(valid.mean()) if len(valid) else 0.0,
            "total_ret_pct": day_total,
            "cum_ret_pct": cum_pnl,
        })
    return _with_equity_and_drawdown(pd.DataFrame(rows), pnl_col="total_ret_pct", order_col="date")


def load_shadow_trade_pnl(date_str: str) -> pd.DataFrame:
    """차단된 BUY의 shadow snapshot 결과를 가상 PnL로 재구성."""
    events_df = load_events(date_str)
    snaps_df = load_price_snapshots(date_str)
    if events_df.empty or snaps_df.empty:
        return pd.DataFrame()

    blocked = events_df[
        (events_df.get("decision_action") == "BUY")
        & (events_df.get("skip_stage") == "GUARDRAIL")
        & (events_df.get("guardrail_result").notna())
    ].copy()
    if blocked.empty:
        return pd.DataFrame()

    shadow_snaps = snaps_df[
        snaps_df["event_id"].astype(str).str.startswith("shadow_", na=False)
    ].copy()
    if shadow_snaps.empty:
        return pd.DataFrame()

    rows = []
    for _, ev in blocked.iterrows():
        original_eid = ev.get("event_id")
        shadow_eid = f"shadow_{original_eid}"
        ev_snaps = shadow_snaps[shadow_snaps["event_id"] == shadow_eid]
        if ev_snaps.empty:
            continue
        t0_row = ev_snaps[ev_snaps["horizon"] == "t0"]
        entry_px = t0_row.iloc[0]["px"] if not t0_row.empty and t0_row.iloc[0].get("px") else None
        best_ret = None
        final_ret = None
        final_horizon = None
        for _, snap in ev_snaps.iterrows():
            ret = _safe_float(snap.get("ret_long_vs_t0"))
            if ret is None:
                continue
            if best_ret is None or ret > best_ret:
                best_ret = ret
            final_ret = ret
            final_horizon = snap.get("horizon")

        rows.append({
            "event_id": original_eid,
            "shadow_event_id": shadow_eid,
            "ticker": ev.get("ticker"),
            "corp_name": ev.get("corp_name"),
            "headline": ev.get("headline", "")[:60],
            "confidence": ev.get("decision_confidence"),
            "size_hint": ev.get("decision_size_hint"),
            "bucket": ev.get("bucket"),
            "detected_at": ev.get("detected_at"),
            "guardrail_result": ev.get("guardrail_result"),
            "entry_px": entry_px,
            "best_ret_pct": round(best_ret * 100, 2) if best_ret is not None else None,
            "final_ret_pct": round(final_ret * 100, 2) if final_ret is not None else None,
            "final_horizon": final_horizon,
        })

    shadow_df = pd.DataFrame(rows)
    if shadow_df.empty:
        return shadow_df
    shadow_df["detected_at"] = pd.to_datetime(shadow_df["detected_at"], errors="coerce")
    return shadow_df.sort_values("detected_at", kind="stable")


def summarize_shadow_trade_pnl(date_str: str) -> dict[str, Any]:
    """shadow snapshot KPI 요약."""
    events_df = load_events(date_str)
    blocked = events_df[
        (events_df.get("decision_action") == "BUY")
        & (events_df.get("skip_stage") == "GUARDRAIL")
        & (events_df.get("guardrail_result").notna())
    ].copy() if not events_df.empty else pd.DataFrame()
    shadow_df = load_shadow_trade_pnl(date_str)

    if shadow_df.empty:
        return {
            "blocked_buy_count": int(len(blocked)) if not blocked.empty else 0,
            "shadow_trade_count": 0,
            "win_rate": 0.0,
            "avg_ret_pct": 0.0,
            "total_ret_pct": 0.0,
            "best_trade_pct": None,
            "top_guardrail_reason": blocked["guardrail_result"].value_counts().index[0]
            if not blocked.empty else None,
        }

    valid = shadow_df["final_ret_pct"].dropna()
    wins = int((valid > 0).sum())
    top_reason = (
        shadow_df["guardrail_result"].value_counts().index[0]
        if "guardrail_result" in shadow_df.columns and not shadow_df["guardrail_result"].dropna().empty
        else None
    )
    return {
        "blocked_buy_count": int(len(blocked)) if not blocked.empty else 0,
        "shadow_trade_count": int(len(shadow_df)),
        "win_rate": wins / len(valid) * 100 if len(valid) else 0.0,
        "avg_ret_pct": float(valid.mean()) if len(valid) else 0.0,
        "total_ret_pct": float(valid.sum()) if len(valid) else 0.0,
        "best_trade_pct": float(valid.max()) if len(valid) else None,
        "top_guardrail_reason": top_reason,
    }


def load_live_feed(limit: int = 40, n_days: int = 3) -> pd.DataFrame:
    """최근 이벤트를 실시간 피드용으로 반환."""
    feed_df = load_multi_day_events(n_days)
    if feed_df.empty:
        return pd.DataFrame()
    if "detected_at" in feed_df.columns:
        feed_df["detected_at"] = pd.to_datetime(feed_df["detected_at"], errors="coerce")
    feed_df["feed_action"] = feed_df.get("effective_action", feed_df.get("decision_action"))
    feed_df["feed_action"] = feed_df["feed_action"].fillna(
        feed_df.get("skip_stage", pd.Series(index=feed_df.index, dtype="object")).fillna("FILTERED")
    )
    ordered = feed_df.sort_values("detected_at", ascending=False, kind="stable").copy()
    cols = [
        "date",
        "detected_at",
        "source",
        "ticker",
        "corp_name",
        "headline",
        "bucket",
        "feed_action",
        "decision_confidence",
        "guardrail_result",
    ]
    available = [col for col in cols if col in ordered.columns]
    return ordered[available].head(limit)


def load_version_trend() -> pd.DataFrame:
    """v64-v65-v66 비교용 릴리스 baseline."""
    latest_metrics = {
        "version": "v66",
        "win_rate": None,
        "total_ret_pct": None,
        "mdd_pct": None,
        "sample_size": 0,
        "source": "latest runtime logs",
        "notes": "최신 실행 BUY 기준 동적 계산",
    }
    dates = available_dates()
    if dates:
        latest_date = dates[0]
        equity_df = compute_daily_equity_curve(latest_date)
        valid = equity_df["final_ret_pct"].dropna() if not equity_df.empty else pd.Series(dtype=float)
        if len(valid):
            latest_metrics.update({
                "win_rate": float((valid > 0).mean() * 100),
                "total_ret_pct": float(valid.sum()),
                "mdd_pct": float(equity_df["drawdown_pct"].min()) if "drawdown_pct" in equity_df.columns else None,
                "sample_size": int(len(valid)),
                "source": f"latest runtime logs ({latest_date})",
                "notes": "로컬 최신 실행 BUY 표본 기준",
            })

    rows = [
        {
            "version": "v64",
            "win_rate": 21.4,
            "total_ret_pct": -17.77,
            "mdd_pct": None,
            "sample_size": 14,
            "source": "docs/reports/performance_analysis_20260327.md",
            "notes": "0310-0327 baseline report",
        },
        {
            "version": "v65",
            "win_rate": 35.7,
            "total_ret_pct": -3.66,
            "mdd_pct": -5.04,
            "sample_size": 14,
            "source": "git show 485238d",
            "notes": "v66 release note에 기록된 v65 baseline",
        },
        latest_metrics,
    ]
    return pd.DataFrame(rows)


# ── DB 기반 분석 로더 ─────────────────────────────────────

def _get_trade_db():
    """TradeDB 인스턴스 반환 (없으면 백필 후 반환)."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from kindshot.trade_db import TradeDB, backfill_from_logs

    db_path = PROJECT_ROOT / "data" / "trade_history.db"
    db = TradeDB(db_path)

    if db.trade_count() == 0:
        backfill_from_logs(
            db,
            LOGS_DIR,
            PROJECT_ROOT / "data" / "runtime" / "price_snapshots",
        )

    return db


def load_db_version_comparison() -> pd.DataFrame:
    """DB 기반 버전별 시뮬레이션 비교 (동일 트레이드셋에 각 버전 파라미터 적용)."""
    try:
        db = _get_trade_db()
        from kindshot.version_report import generate_version_comparison
        metrics = generate_version_comparison(db)
        db.close()
        rows = []
        for m in metrics:
            rows.append({
                "version": m.version,
                "trades": m.total_trades,
                "wins": m.wins,
                "losses": m.losses,
                "win_rate": m.win_rate,
                "avg_ret_pct": m.avg_ret_pct,
                "total_ret_pct": m.total_ret_pct,
                "profit_factor": m.profit_factor,
                "mdd_pct": m.mdd_pct,
                "description": m.description,
            })
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def load_db_ticker_summary() -> pd.DataFrame:
    """DB 기반 종목별 성과."""
    try:
        db = _get_trade_db()
        rows = db.ticker_summary()
        db.close()
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def load_db_hour_summary() -> pd.DataFrame:
    """DB 기반 시간대별 성과."""
    try:
        db = _get_trade_db()
        rows = db.hour_summary()
        db.close()
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def load_db_category_summary() -> pd.DataFrame:
    """DB 기반 카테고리별 성과."""
    try:
        db = _get_trade_db()
        rows = db.category_summary()
        db.close()
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def load_db_daily_summary() -> pd.DataFrame:
    """DB 기반 일별 성과."""
    try:
        db = _get_trade_db()
        rows = db.daily_summary()
        db.close()
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def load_db_exit_type_summary() -> pd.DataFrame:
    """DB 기반 청산 유형별 성과."""
    try:
        db = _get_trade_db()
        rows = db.exit_type_summary()
        db.close()
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def load_db_version_x_hour() -> pd.DataFrame:
    """DB 기반 버전 × 시간대 교차 매트릭스."""
    try:
        db = _get_trade_db()
        rows = db.version_x_hour()
        db.close()
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()
