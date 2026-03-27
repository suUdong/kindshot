"""트레이드 히스토리 SQLite DB — 로그 백필 + 분석 쿼리."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

CREATE_TRADES_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    event_id        TEXT PRIMARY KEY,
    date            TEXT NOT NULL,           -- YYYYMMDD
    detected_at     TEXT,                    -- ISO timestamp
    ticker          TEXT NOT NULL,
    corp_name       TEXT DEFAULT '',
    headline        TEXT DEFAULT '',
    bucket          TEXT DEFAULT '',
    keyword_hits    TEXT DEFAULT '[]',       -- JSON array
    news_category   TEXT DEFAULT '',
    decision_action TEXT DEFAULT 'BUY',
    confidence      INTEGER DEFAULT 0,
    size_hint       TEXT DEFAULT 'M',
    decision_reason TEXT DEFAULT '',
    decision_source TEXT DEFAULT '',
    guardrail_result TEXT DEFAULT '',
    skip_stage      TEXT DEFAULT '',

    -- quant context
    adv_value_20d   REAL,
    spread_bps      REAL,
    ret_today       REAL,
    rsi_14          REAL,
    vol_pct_20d     REAL,

    -- market context
    kospi_change_pct  REAL,
    kosdaq_change_pct REAL,
    kospi_breadth     REAL,
    kosdaq_breadth    REAL,

    -- price snapshot results (ret % vs t0)
    ret_t0          REAL,
    ret_t30s        REAL,
    ret_t1m         REAL,
    ret_t2m         REAL,
    ret_t5m         REAL,
    ret_t10m        REAL,
    ret_t15m        REAL,
    ret_t20m        REAL,
    ret_t30m        REAL,
    ret_close       REAL,
    entry_px        REAL,

    -- simulated exit
    exit_type       TEXT DEFAULT '',         -- TP, SL, TRAILING, TIMEOUT, T5M_LOSS
    exit_horizon    TEXT DEFAULT '',
    exit_ret_pct    REAL,
    peak_ret_pct    REAL,

    -- version tag (inferred from strategy params active on that date)
    version_tag     TEXT DEFAULT '',

    -- hour slot for time-of-day analysis
    hour_slot       INTEGER DEFAULT 0,       -- 9, 10, 11, ...

    created_at      TEXT DEFAULT (datetime('now'))
);
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date);",
    "CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);",
    "CREATE INDEX IF NOT EXISTS idx_trades_bucket ON trades(bucket);",
    "CREATE INDEX IF NOT EXISTS idx_trades_version ON trades(version_tag);",
    "CREATE INDEX IF NOT EXISTS idx_trades_hour ON trades(hour_slot);",
    "CREATE INDEX IF NOT EXISTS idx_trades_category ON trades(news_category);",
]

CREATE_META_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# 버전-날짜 매핑: 실제 서버 배포 기준
# pre-v59: 20260310-20260326 (초기 전략)
# v59-v64: 2026-03-27 04:00-04:08 커밋 (파라미터 튜닝)
# v65: 2026-03-27 16:13 커밋 (SL/TP 비대칭 수정)
# v66: 2026-03-27 16:31 커밋 (confidence 감점 완화)
# v67: 2026-03-27 20:32 커밋 (변동성 레짐)
# v68: 2026-03-27 21:53 커밋 (뉴스소스 확장)
# v69: 2026-03-27 23:02 커밋 (VTS stale exit)
VERSION_MAP: list[dict[str, Any]] = [
    {"tag": "pre-v59", "dates": ["20260310", "20260311", "20260312", "20260313",
                                  "20260316", "20260317", "20260318", "20260319",
                                  "20260320", "20260323", "20260324", "20260325",
                                  "20260326"],
     "description": "초기 전략 (v44~v58)"},
    {"tag": "v64", "dates": ["20260327"], "description": "대형주 preflight 바이패스, confidence 기본 상향"},
    {"tag": "v65", "dates": [], "description": "SL/TP 비대칭 수정, trailing stop 개선"},
    {"tag": "v66", "dates": [], "description": "confidence 감점 완화, 시간대 보정, shadow snapshot"},
    {"tag": "v67", "dates": [], "description": "변동성 레짐 동적 confidence, 뉴스 카테고리"},
    {"tag": "v68", "dates": [], "description": "뉴스소스 확장, 멀티타임프레임, 종목별 학습"},
    {"tag": "v69", "dates": [], "description": "VTS stale exit, prompt enrichment"},
    {"tag": "v70", "dates": [], "description": "max_hold 20분, trailing 1.0%, 고배당/재공시 IGNORE"},
]

# strategy parameter sets per version (for retroactive simulation)
VERSION_PARAMS: dict[str, dict[str, Any]] = {
    "pre-v59": {
        "paper_take_profit_pct": 1.0,
        "paper_stop_loss_pct": -0.7,
        "trailing_stop_activation_pct": 0.3,
        "trailing_stop_early_pct": 0.3,
        "trailing_stop_mid_pct": 0.5,
        "trailing_stop_late_pct": 0.7,
        "max_hold_minutes": 10,
        "min_buy_confidence": 75,
    },
    "v64": {
        "paper_take_profit_pct": 1.0,
        "paper_stop_loss_pct": -0.7,
        "trailing_stop_activation_pct": 0.3,
        "trailing_stop_early_pct": 0.3,
        "trailing_stop_mid_pct": 0.5,
        "trailing_stop_late_pct": 0.7,
        "max_hold_minutes": 10,
        "min_buy_confidence": 78,
    },
    "v65": {
        "paper_take_profit_pct": 2.0,
        "paper_stop_loss_pct": -1.5,
        "trailing_stop_activation_pct": 0.5,
        "trailing_stop_early_pct": 0.5,
        "trailing_stop_mid_pct": 0.8,
        "trailing_stop_late_pct": 1.0,
        "max_hold_minutes": 15,
        "min_buy_confidence": 78,
    },
    "v66": {
        "paper_take_profit_pct": 2.0,
        "paper_stop_loss_pct": -1.5,
        "trailing_stop_activation_pct": 0.5,
        "trailing_stop_early_pct": 0.5,
        "trailing_stop_mid_pct": 0.8,
        "trailing_stop_late_pct": 1.0,
        "max_hold_minutes": 15,
        "min_buy_confidence": 78,
    },
    "v67": {
        "paper_take_profit_pct": 2.0,
        "paper_stop_loss_pct": -1.5,
        "trailing_stop_activation_pct": 0.5,
        "trailing_stop_early_pct": 0.5,
        "trailing_stop_mid_pct": 0.8,
        "trailing_stop_late_pct": 1.0,
        "max_hold_minutes": 15,
        "min_buy_confidence": 78,
    },
    "v68": {
        "paper_take_profit_pct": 2.0,
        "paper_stop_loss_pct": -1.5,
        "trailing_stop_activation_pct": 0.5,
        "trailing_stop_early_pct": 0.5,
        "trailing_stop_mid_pct": 0.8,
        "trailing_stop_late_pct": 1.0,
        "max_hold_minutes": 15,
        "min_buy_confidence": 78,
    },
    "v69": {
        "paper_take_profit_pct": 2.0,
        "paper_stop_loss_pct": -1.5,
        "trailing_stop_activation_pct": 0.5,
        "trailing_stop_early_pct": 0.5,
        "trailing_stop_mid_pct": 0.8,
        "trailing_stop_late_pct": 1.0,
        "max_hold_minutes": 15,
        "min_buy_confidence": 78,
    },
    "v70": {
        "paper_take_profit_pct": 2.0,
        "paper_stop_loss_pct": -1.5,
        "trailing_stop_activation_pct": 0.5,
        "trailing_stop_early_pct": 0.5,
        "trailing_stop_mid_pct": 0.8,
        "trailing_stop_late_pct": 1.0,
        "max_hold_minutes": 20,
        "trailing_stop_pct": 1.0,
        "min_buy_confidence": 78,
    },
}


@dataclass
class TradeRow:
    """DB에서 읽은 트레이드 레코드."""
    event_id: str
    date: str
    detected_at: str
    ticker: str
    corp_name: str
    headline: str
    bucket: str
    keyword_hits: list[str]
    news_category: str
    confidence: int
    size_hint: str
    decision_reason: str
    guardrail_result: str
    ret_t5m: Optional[float]
    ret_t10m: Optional[float]
    ret_close: Optional[float]
    exit_type: str
    exit_ret_pct: Optional[float]
    peak_ret_pct: Optional[float]
    version_tag: str
    hour_slot: int


class TradeDB:
    """SQLite 기반 트레이드 히스토리."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(CREATE_META_SQL)
        cur.execute(CREATE_TRADES_SQL)
        for idx_sql in CREATE_INDEXES_SQL:
            cur.execute(idx_sql)
        cur.execute("INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)",
                     ("schema_version", str(SCHEMA_VERSION)))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def trade_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM trades").fetchone()
        return row[0] if row else 0

    def has_date(self, date: str) -> bool:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM trades WHERE date = ?", (date,)
        ).fetchone()
        return (row[0] or 0) > 0

    def upsert_trade(self, data: dict[str, Any]) -> None:
        """Insert or replace a trade record."""
        cols = [
            "event_id", "date", "detected_at", "ticker", "corp_name", "headline",
            "bucket", "keyword_hits", "news_category", "decision_action",
            "confidence", "size_hint", "decision_reason", "decision_source",
            "guardrail_result", "skip_stage",
            "adv_value_20d", "spread_bps", "ret_today", "rsi_14", "vol_pct_20d",
            "kospi_change_pct", "kosdaq_change_pct", "kospi_breadth", "kosdaq_breadth",
            "ret_t0", "ret_t30s", "ret_t1m", "ret_t2m", "ret_t5m",
            "ret_t10m", "ret_t15m", "ret_t20m", "ret_t30m", "ret_close",
            "entry_px", "exit_type", "exit_horizon", "exit_ret_pct", "peak_ret_pct",
            "version_tag", "hour_slot",
        ]
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        vals = [data.get(c) for c in cols]
        self._conn.execute(
            f"INSERT OR REPLACE INTO trades({col_names}) VALUES({placeholders})",
            vals,
        )

    def commit(self) -> None:
        self._conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Execute arbitrary SELECT and return list of dicts."""
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def version_summary(self) -> list[dict[str, Any]]:
        """버전별 성과 요약."""
        return self.query("""
            SELECT
                version_tag,
                COUNT(*) as total_trades,
                SUM(CASE WHEN exit_ret_pct > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN exit_ret_pct <= 0 THEN 1 ELSE 0 END) as losses,
                ROUND(AVG(exit_ret_pct), 4) as avg_ret_pct,
                ROUND(SUM(exit_ret_pct), 4) as total_ret_pct,
                ROUND(MAX(exit_ret_pct), 4) as max_win_pct,
                ROUND(MIN(exit_ret_pct), 4) as max_loss_pct,
                ROUND(AVG(confidence), 1) as avg_confidence,
                ROUND(AVG(peak_ret_pct), 4) as avg_peak_pct
            FROM trades
            WHERE exit_ret_pct IS NOT NULL
            GROUP BY version_tag
            ORDER BY version_tag
        """)

    def ticker_summary(self) -> list[dict[str, Any]]:
        """종목별 성과 요약."""
        return self.query("""
            SELECT
                ticker, corp_name,
                COUNT(*) as trades,
                SUM(CASE WHEN exit_ret_pct > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(AVG(exit_ret_pct), 4) as avg_ret_pct,
                ROUND(SUM(exit_ret_pct), 4) as total_ret_pct
            FROM trades
            WHERE exit_ret_pct IS NOT NULL
            GROUP BY ticker
            ORDER BY total_ret_pct DESC
        """)

    def hour_summary(self) -> list[dict[str, Any]]:
        """시간대별 성과 요약."""
        return self.query("""
            SELECT
                hour_slot,
                COUNT(*) as trades,
                SUM(CASE WHEN exit_ret_pct > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(AVG(exit_ret_pct), 4) as avg_ret_pct,
                ROUND(SUM(exit_ret_pct), 4) as total_ret_pct
            FROM trades
            WHERE exit_ret_pct IS NOT NULL
            GROUP BY hour_slot
            ORDER BY hour_slot
        """)

    def category_summary(self) -> list[dict[str, Any]]:
        """뉴스 카테고리별 성과 요약."""
        return self.query("""
            SELECT
                COALESCE(NULLIF(news_category, ''), bucket) as category,
                COUNT(*) as trades,
                SUM(CASE WHEN exit_ret_pct > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(AVG(exit_ret_pct), 4) as avg_ret_pct,
                ROUND(SUM(exit_ret_pct), 4) as total_ret_pct,
                ROUND(AVG(confidence), 1) as avg_confidence
            FROM trades
            WHERE exit_ret_pct IS NOT NULL
            GROUP BY category
            ORDER BY total_ret_pct DESC
        """)

    def daily_summary(self) -> list[dict[str, Any]]:
        """일별 성과 요약."""
        return self.query("""
            SELECT
                date,
                version_tag,
                COUNT(*) as trades,
                SUM(CASE WHEN exit_ret_pct > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(AVG(exit_ret_pct), 4) as avg_ret_pct,
                ROUND(SUM(exit_ret_pct), 4) as total_ret_pct,
                ROUND(AVG(confidence), 1) as avg_confidence
            FROM trades
            WHERE exit_ret_pct IS NOT NULL
            GROUP BY date
            ORDER BY date
        """)

    def exit_type_summary(self) -> list[dict[str, Any]]:
        """청산 유형별 성과."""
        return self.query("""
            SELECT
                exit_type,
                COUNT(*) as trades,
                ROUND(AVG(exit_ret_pct), 4) as avg_ret_pct,
                ROUND(SUM(exit_ret_pct), 4) as total_ret_pct
            FROM trades
            WHERE exit_ret_pct IS NOT NULL AND exit_type != ''
            GROUP BY exit_type
            ORDER BY total_ret_pct DESC
        """)

    def version_x_hour(self) -> list[dict[str, Any]]:
        """버전 × 시간대 교차 매트릭스."""
        return self.query("""
            SELECT
                version_tag, hour_slot,
                COUNT(*) as trades,
                SUM(CASE WHEN exit_ret_pct > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(AVG(exit_ret_pct), 4) as avg_ret_pct
            FROM trades
            WHERE exit_ret_pct IS NOT NULL
            GROUP BY version_tag, hour_slot
            ORDER BY version_tag, hour_slot
        """)


def _version_for_date(date_str: str) -> str:
    """날짜 → 버전 태그 매핑."""
    for vmap in VERSION_MAP:
        if date_str in vmap["dates"]:
            return vmap["tag"]
    return "pre-v59"


def _parse_hour(detected_at: str) -> int:
    """ISO timestamp에서 시간(hour) 추출."""
    if not detected_at:
        return 0
    try:
        dt = datetime.fromisoformat(detected_at)
        return dt.hour
    except (ValueError, TypeError):
        return 0


def backfill_from_logs(
    db: TradeDB,
    logs_dir: Path,
    snapshots_dir: Path,
    *,
    force: bool = False,
) -> int:
    """JSONL 로그에서 BUY 이벤트를 파싱하여 DB에 백필.

    Returns: 저장된 트레이드 수
    """
    from kindshot.strategy_observability import classify_buy_exit, StrategyReportConfig

    log_files = sorted(logs_dir.glob("kindshot_*.jsonl"))
    total_inserted = 0

    for log_file in log_files:
        date_str = log_file.stem.replace("kindshot_", "")
        if not date_str.isdigit() or len(date_str) != 8:
            continue

        if not force and db.has_date(date_str):
            logger.debug("Skipping %s (already backfilled)", date_str)
            continue

        # 이벤트 로드
        events: dict[str, dict[str, Any]] = {}
        decisions: dict[str, dict[str, Any]] = {}
        snap_by_event: dict[str, dict[str, dict[str, Any]]] = {}
        with open(log_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rtype = record.get("type")
                eid = record.get("event_id", "")
                if rtype == "event" and eid:
                    events[eid] = record
                elif rtype == "decision" and eid:
                    decisions[eid] = record
                elif rtype == "price_snapshot" and eid:
                    horizon = str(record.get("horizon", "")).strip()
                    if horizon:
                        snap_by_event.setdefault(eid, {})[horizon] = record

        # BUY 이벤트 필터
        buy_events = {
            eid: ev for eid, ev in events.items()
            if ev.get("decision_action") == "BUY"
        }
        if not buy_events:
            continue

        # 스냅샷 로드: 로그 내 embedded snapshot을 기본으로 두고,
        # 별도 runtime snapshot 파일이 있으면 해당 horizon을 overlay 한다.
        snap_path = snapshots_dir / f"{date_str}.jsonl"
        if snap_path.exists():
            with open(snap_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        snap = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    eid = snap.get("event_id", "")
                    horizon = snap.get("horizon", "")
                    if eid and horizon:
                        snap_by_event.setdefault(eid, {})[horizon] = snap

        version_tag = _version_for_date(date_str)

        # 각 버전의 strategy params로 exit 시뮬레이션
        params = VERSION_PARAMS.get(version_tag, VERSION_PARAMS["pre-v59"])
        strat_config = StrategyReportConfig(
            paper_take_profit_pct=params["paper_take_profit_pct"],
            paper_stop_loss_pct=params["paper_stop_loss_pct"],
            trailing_stop_activation_pct=params["trailing_stop_activation_pct"],
            trailing_stop_early_pct=params["trailing_stop_early_pct"],
            trailing_stop_mid_pct=params["trailing_stop_mid_pct"],
            trailing_stop_late_pct=params["trailing_stop_late_pct"],
            max_hold_minutes=params["max_hold_minutes"],
        )

        for eid, ev in buy_events.items():
            ctx = ev.get("ctx") or {}
            mctx = ev.get("market_ctx") or {}
            snaps = snap_by_event.get(eid, {})

            # 스냅샷에서 ret % 추출
            def _snap_ret(h: str) -> Optional[float]:
                s = snaps.get(h)
                if s and s.get("ret_long_vs_t0") is not None:
                    return round(s["ret_long_vs_t0"] * 100, 4)
                return None

            t0_snap = snaps.get("t0", {})
            entry_px = t0_snap.get("px")

            # exit 시뮬레이션
            exit_type, exit_horizon = classify_buy_exit(ev, snaps, config=strat_config)

            # exit ret 계산
            exit_ret = None
            peak_ret = None
            if exit_horizon and snaps.get(exit_horizon):
                r = snaps[exit_horizon].get("ret_long_vs_t0")
                if r is not None:
                    exit_ret = round(r * 100, 4)

            # peak 계산
            all_rets = []
            for h_snaps in snaps.values():
                r = h_snaps.get("ret_long_vs_t0")
                if r is not None:
                    all_rets.append(r * 100)
            if all_rets:
                peak_ret = round(max(all_rets), 4)

            # exit 없으면 close 또는 마지막 스냅샷 사용
            if exit_ret is None:
                for fallback_h in ["close", "t+30m", "t+20m", "t+15m", "t+10m", "t+5m"]:
                    r = _snap_ret(fallback_h)
                    if r is not None:
                        exit_ret = r
                        exit_type = exit_type or "timeout"
                        exit_horizon = exit_horizon or fallback_h
                        break

            kw_hits = ev.get("keyword_hits") or []
            detected_at = ev.get("detected_at", "")
            hour_slot = _parse_hour(detected_at)

            data = {
                "event_id": eid,
                "date": date_str,
                "detected_at": detected_at,
                "ticker": ev.get("ticker", ""),
                "corp_name": ev.get("corp_name", ""),
                "headline": ev.get("headline", ""),
                "bucket": ev.get("bucket", ""),
                "keyword_hits": json.dumps(kw_hits, ensure_ascii=False),
                "news_category": ev.get("news_category", ""),
                "decision_action": "BUY",
                "confidence": ev.get("decision_confidence", 0),
                "size_hint": ev.get("decision_size_hint", "M"),
                "decision_reason": ev.get("decision_reason", ""),
                "decision_source": "",
                "guardrail_result": ev.get("guardrail_result", ""),
                "skip_stage": ev.get("skip_stage", ""),
                "adv_value_20d": ctx.get("adv_value_20d"),
                "spread_bps": ctx.get("spread_bps"),
                "ret_today": ctx.get("ret_today"),
                "rsi_14": ctx.get("rsi_14"),
                "vol_pct_20d": ctx.get("vol_pct_20d"),
                "kospi_change_pct": mctx.get("kospi_change_pct"),
                "kosdaq_change_pct": mctx.get("kosdaq_change_pct"),
                "kospi_breadth": mctx.get("kospi_breadth_ratio"),
                "kosdaq_breadth": mctx.get("kosdaq_breadth_ratio"),
                "ret_t0": _snap_ret("t0"),
                "ret_t30s": _snap_ret("t+30s"),
                "ret_t1m": _snap_ret("t+1m"),
                "ret_t2m": _snap_ret("t+2m"),
                "ret_t5m": _snap_ret("t+5m"),
                "ret_t10m": _snap_ret("t+10m"),
                "ret_t15m": _snap_ret("t+15m"),
                "ret_t20m": _snap_ret("t+20m"),
                "ret_t30m": _snap_ret("t+30m"),
                "ret_close": _snap_ret("close"),
                "entry_px": entry_px,
                "exit_type": exit_type or "",
                "exit_horizon": exit_horizon or "",
                "exit_ret_pct": exit_ret,
                "peak_ret_pct": peak_ret,
                "version_tag": version_tag,
                "hour_slot": hour_slot,
            }
            db.upsert_trade(data)
            total_inserted += 1

        db.commit()
        logger.info("Backfilled %s: %d BUY trades", date_str, len(buy_events))

    return total_inserted


def simulate_version_on_trades(
    db: TradeDB,
    version_tag: str,
) -> list[dict[str, Any]]:
    """기존 트레이드에 특정 버전의 strategy params를 적용하여 가상 exit 계산.

    모든 BUY 이벤트에 대해 해당 버전의 TP/SL/trailing 파라미터로 재시뮬레이션.
    """
    from kindshot.strategy_observability import classify_buy_exit, StrategyReportConfig

    params = VERSION_PARAMS.get(version_tag)
    if not params:
        return []

    strat_config = StrategyReportConfig(
        paper_take_profit_pct=params["paper_take_profit_pct"],
        paper_stop_loss_pct=params["paper_stop_loss_pct"],
        trailing_stop_activation_pct=params["trailing_stop_activation_pct"],
        trailing_stop_early_pct=params["trailing_stop_early_pct"],
        trailing_stop_mid_pct=params["trailing_stop_mid_pct"],
        trailing_stop_late_pct=params["trailing_stop_late_pct"],
        max_hold_minutes=params["max_hold_minutes"],
    )

    # DB에서 모든 트레이드 + 스냅샷 데이터 조회
    trades = db.query("""
        SELECT event_id, date, ticker, headline, keyword_hits, confidence,
               ret_t0, ret_t30s, ret_t1m, ret_t2m, ret_t5m,
               ret_t10m, ret_t15m, ret_t20m, ret_t30m, ret_close,
               bucket, guardrail_result
        FROM trades
    """)

    results = []
    horizon_map = {
        "ret_t0": "t0", "ret_t30s": "t+30s", "ret_t1m": "t+1m",
        "ret_t2m": "t+2m", "ret_t5m": "t+5m", "ret_t10m": "t+10m",
        "ret_t15m": "t+15m", "ret_t20m": "t+20m", "ret_t30m": "t+30m",
        "ret_close": "close",
    }

    for trade in trades:
        # 스냅샷 dict 재구성
        snapshots: dict[str, dict[str, Any]] = {}
        for col, horizon in horizon_map.items():
            val = trade.get(col)
            if val is not None:
                snapshots[horizon] = {"ret_long_vs_t0": val / 100.0}

        kw_hits = json.loads(trade.get("keyword_hits", "[]"))
        event_like = {
            "headline": trade.get("headline", ""),
            "keyword_hits": kw_hits,
            "bucket": trade.get("bucket", ""),
        }

        exit_type, exit_horizon = classify_buy_exit(event_like, snapshots, config=strat_config)

        exit_ret = None
        if exit_horizon and snapshots.get(exit_horizon):
            exit_ret = round(snapshots[exit_horizon]["ret_long_vs_t0"] * 100, 4)

        # fallback
        if exit_ret is None:
            for fb in ["close", "t+30m", "t+20m", "t+15m", "t+10m", "t+5m"]:
                if fb in snapshots:
                    exit_ret = round(snapshots[fb]["ret_long_vs_t0"] * 100, 4)
                    exit_type = exit_type or "timeout"
                    break

        results.append({
            "event_id": trade["event_id"],
            "date": trade["date"],
            "ticker": trade["ticker"],
            "version_tag": version_tag,
            "exit_type": exit_type or "",
            "exit_horizon": exit_horizon or "",
            "exit_ret_pct": exit_ret,
            "confidence": trade.get("confidence"),
            "guardrail_result": trade.get("guardrail_result", ""),
        })

    return results
