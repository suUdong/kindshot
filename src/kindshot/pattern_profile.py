"""Recent trade pattern profiling for bounded runtime confidence/guardrail tuning."""

from __future__ import annotations

import importlib.util
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from kindshot.config import Config
from kindshot.news_category import NEWS_TYPE_RULES, classify_news_type
from kindshot.trade_db import TradeDB, backfill_from_logs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PatternCohort:
    pattern_type: str
    key: str
    news_type: str | None
    ticker: str | None
    hour_bucket: str | None
    count: int
    wins: int
    losses: int
    win_rate: float
    avg_pnl_pct: float
    total_pnl_pct: float
    confidence_delta: int = 0
    guardrail_reason: str | None = None

    def matches(self, *, news_type: str, ticker: str, hour_bucket: str) -> bool:
        if self.news_type is not None and self.news_type != news_type:
            return False
        if self.ticker is not None and self.ticker != ticker:
            return False
        if self.hour_bucket is not None and self.hour_bucket != hour_bucket:
            return False
        return True

    def to_summary(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecentPatternProfile:
    enabled: bool
    analysis_dates: tuple[str, ...]
    total_trades: int
    boost_patterns: tuple[PatternCohort, ...]
    loss_guardrail_patterns: tuple[PatternCohort, ...]
    top_profit_exact: PatternCohort | None = None
    top_loss_exact: PatternCohort | None = None

    @classmethod
    def empty(cls) -> "RecentPatternProfile":
        return cls(
            enabled=False,
            analysis_dates=(),
            total_trades=0,
            boost_patterns=(),
            loss_guardrail_patterns=(),
            top_profit_exact=None,
            top_loss_exact=None,
        )

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "analysis_dates": list(self.analysis_dates),
            "total_trades": self.total_trades,
            "boost_patterns": [row.to_summary() for row in self.boost_patterns],
            "loss_guardrail_patterns": [row.to_summary() for row in self.loss_guardrail_patterns],
            "top_profit_exact": self.top_profit_exact.to_summary() if self.top_profit_exact else None,
            "top_loss_exact": self.top_loss_exact.to_summary() if self.top_loss_exact else None,
        }


def _parse_keyword_hits(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


def _hour_bucket(hour: int) -> str:
    if hour < 0:
        return "unknown"
    if hour < 9:
        return "pre_open"
    if hour == 9:
        return "open"
    if hour == 10:
        return "mid_morning"
    if 11 <= hour <= 13:
        return "midday"
    if hour == 14:
        return "afternoon"
    return "late"


def _row_news_type(row: dict[str, Any]) -> str:
    category = str(row.get("news_category") or "").strip()
    if category and any(category == known for known, _patterns in NEWS_TYPE_RULES):
        return category
    return classify_news_type(
        str(row.get("headline") or ""),
        _parse_keyword_hits(row.get("keyword_hits")),
    )


def _normalize_trade_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        pnl = row.get("exit_ret_pct")
        if not isinstance(pnl, (int, float)):
            continue
        hour_slot = int(row.get("hour_slot") or 0)
        normalized.append(
            {
                "date": str(row.get("date") or ""),
                "ticker": str(row.get("ticker") or ""),
                "headline": str(row.get("headline") or ""),
                "news_type": _row_news_type(row),
                "hour_bucket": _hour_bucket(hour_slot),
                "exit_ret_pct": float(pnl),
            }
        )
    return normalized


def _build_cohorts(
    rows: list[dict[str, Any]],
    *,
    pattern_type: str,
    key_fields: tuple[str, ...],
) -> list[PatternCohort]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in key_fields)
        grouped.setdefault(key, []).append(row)

    cohorts: list[PatternCohort] = []
    for key, members in grouped.items():
        pnls = [float(member["exit_ret_pct"]) for member in members]
        wins = sum(1 for value in pnls if value > 0)
        count = len(members)
        losses = count - wins
        cohorts.append(
            PatternCohort(
                pattern_type=pattern_type,
                key="|".join(key),
                news_type=members[0]["news_type"] if "news_type" in key_fields else None,
                ticker=members[0]["ticker"] if "ticker" in key_fields else None,
                hour_bucket=members[0]["hour_bucket"] if "hour_bucket" in key_fields else None,
                count=count,
                wins=wins,
                losses=losses,
                win_rate=(wins / count) if count else 0.0,
                avg_pnl_pct=(sum(pnls) / count) if count else 0.0,
                total_pnl_pct=sum(pnls),
            )
        )
    return cohorts


def _cohort_specificity(cohort: PatternCohort) -> int:
    return sum(value is not None for value in (cohort.news_type, cohort.ticker, cohort.hour_bucket))


def _sorted_profit_cohorts(cohorts: list[PatternCohort]) -> list[PatternCohort]:
    return sorted(
        cohorts,
        key=lambda row: (_cohort_specificity(row), row.total_pnl_pct, row.win_rate, row.count),
        reverse=True,
    )


def _sorted_loss_cohorts(cohorts: list[PatternCohort]) -> list[PatternCohort]:
    return sorted(
        cohorts,
        key=lambda row: (_cohort_specificity(row), abs(row.total_pnl_pct), row.count, -row.win_rate),
        reverse=True,
    )


def build_recent_pattern_profile_from_rows(rows: list[dict[str, Any]], config: Config) -> RecentPatternProfile:
    normalized = _normalize_trade_rows(rows)
    if not config.recent_pattern_enabled or not normalized:
        return RecentPatternProfile.empty()

    exact = _build_cohorts(
        normalized,
        pattern_type="news_type_ticker_hour_bucket",
        key_fields=("news_type", "ticker", "hour_bucket"),
    )
    news_ticker = _build_cohorts(
        normalized,
        pattern_type="news_type_ticker",
        key_fields=("news_type", "ticker"),
    )
    news_hour = _build_cohorts(
        normalized,
        pattern_type="news_type_hour_bucket",
        key_fields=("news_type", "hour_bucket"),
    )
    hour_only = _build_cohorts(
        normalized,
        pattern_type="hour_bucket",
        key_fields=("hour_bucket",),
    )

    top_profit_exact = max(exact, key=lambda row: (row.total_pnl_pct, row.win_rate, row.count), default=None)
    top_loss_exact = min(exact, key=lambda row: (row.total_pnl_pct, row.win_rate, -row.count), default=None)

    news_hour_map = {(cohort.news_type, cohort.hour_bucket): cohort for cohort in news_hour}

    boost_candidates: list[PatternCohort] = []
    for cohort in exact:
        if cohort.count < config.recent_pattern_min_trades:
            continue
        if cohort.total_pnl_pct < config.recent_pattern_profit_min_total_pnl_pct:
            continue
        broader = news_hour_map.get((cohort.news_type, cohort.hour_bucket))
        if broader is None or broader.total_pnl_pct < 0:
            continue
        boost_candidates.append(
            PatternCohort(
                **{**cohort.to_summary(), "confidence_delta": config.recent_pattern_profit_boost}
            )
        )

    if not boost_candidates:
        stable_news_hour = [
            cohort
            for cohort in news_hour
            if cohort.count >= config.recent_pattern_min_trades
            and cohort.win_rate >= config.recent_pattern_profit_min_win_rate
            and cohort.total_pnl_pct >= config.recent_pattern_profit_min_total_pnl_pct
        ]
        stable_hour_only = [
            cohort
            for cohort in hour_only
            if cohort.count >= config.recent_pattern_min_trades
            and cohort.win_rate >= config.recent_pattern_profit_min_win_rate
            and cohort.total_pnl_pct >= config.recent_pattern_profit_min_total_pnl_pct
        ]

        for cohort in stable_news_hour:
            boost_candidates.append(
                PatternCohort(
                    **{**cohort.to_summary(), "confidence_delta": config.recent_pattern_profit_boost}
                )
            )
        if not boost_candidates:
            for cohort in stable_hour_only:
                boost_candidates.append(
                    PatternCohort(
                        **{**cohort.to_summary(), "confidence_delta": config.recent_pattern_profit_boost}
                    )
                )

    ticker_hour = _build_cohorts(
        normalized,
        pattern_type="ticker_hour_bucket",
        key_fields=("ticker", "hour_bucket"),
    )
    loss_candidates: list[PatternCohort] = []
    for cohort in (*news_hour, *news_ticker, *ticker_hour):
        if cohort.count < config.recent_pattern_min_trades:
            continue
        if cohort.win_rate > config.recent_pattern_loss_max_win_rate:
            continue
        if cohort.total_pnl_pct > config.recent_pattern_loss_max_total_pnl_pct:
            continue
        loss_candidates.append(
            PatternCohort(
                **{**cohort.to_summary(), "guardrail_reason": "PATTERN_LOSS_GUARDRAIL"}
            )
        )

    analysis_dates = tuple(sorted({row["date"] for row in normalized}))
    return RecentPatternProfile(
        enabled=bool(boost_candidates or loss_candidates),
        analysis_dates=analysis_dates,
        total_trades=len(normalized),
        boost_patterns=tuple(_sorted_profit_cohorts(boost_candidates)[: config.recent_pattern_max_profit_patterns]),
        loss_guardrail_patterns=tuple(_sorted_loss_cohorts(loss_candidates)[: config.recent_pattern_max_loss_patterns]),
        top_profit_exact=top_profit_exact,
        top_loss_exact=top_loss_exact,
    )


def build_recent_pattern_profile(config: Config) -> RecentPatternProfile:
    if not config.recent_pattern_enabled:
        return RecentPatternProfile.empty()

    profile = _build_recent_pattern_profile_from_backtest(config)
    if profile.enabled:
        _persist_profile_summary(config.recent_pattern_profile_path, profile)
        logger.info(
            "RecentPatternProfile loaded: dates=%s trades=%d boost=%d loss=%d",
            ",".join(profile.analysis_dates),
            profile.total_trades,
            len(profile.boost_patterns),
            len(profile.loss_guardrail_patterns),
        )
        return profile

    db = TradeDB(config.data_dir / "trade_history.db")
    try:
        backfill_from_logs(
            db,
            config.log_dir,
            config.runtime_price_snapshots_dir,
            force=True,
        )
        date_rows = db.query(
            """
            SELECT DISTINCT date
            FROM trades
            WHERE exit_ret_pct IS NOT NULL
            ORDER BY date DESC
            LIMIT ?
            """,
            (config.recent_pattern_lookback_days,),
        )
        dates = [str(row["date"]) for row in date_rows]
        if not dates:
            return RecentPatternProfile.empty()
        placeholders = ", ".join(["?"] * len(dates))
        rows = db.query(
            f"""
            SELECT date, ticker, headline, keyword_hits, news_category, hour_slot, exit_ret_pct
            FROM trades
            WHERE exit_ret_pct IS NOT NULL
              AND date IN ({placeholders})
            """,
            tuple(dates),
        )
    finally:
        db.close()

    profile = build_recent_pattern_profile_from_rows(rows, config)
    _persist_profile_summary(config.recent_pattern_profile_path, profile)
    if profile.enabled:
        logger.info(
            "RecentPatternProfile loaded: dates=%s trades=%d boost=%d loss=%d",
            ",".join(profile.analysis_dates),
            profile.total_trades,
            len(profile.boost_patterns),
            len(profile.loss_guardrail_patterns),
        )
    else:
        logger.info("RecentPatternProfile inactive: no eligible recent cohorts")
    return profile


def _load_backtest_analysis_module() -> Any | None:
    candidates = [
        Path.cwd() / "scripts" / "backtest_analysis.py",
        Path(__file__).resolve().parents[2] / "scripts" / "backtest_analysis.py",
    ]
    for script_path in candidates:
        if not script_path.exists():
            continue
        spec = importlib.util.spec_from_file_location("kindshot_backtest_analysis_runtime", script_path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return None


def _build_recent_pattern_profile_from_backtest(config: Config) -> RecentPatternProfile:
    module = _load_backtest_analysis_module()
    if module is None:
        return RecentPatternProfile.empty()

    log_paths = sorted(config.log_dir.glob("kindshot_*.jsonl"))
    if not log_paths:
        return RecentPatternProfile.empty()
    recent_paths = log_paths[-max(1, config.recent_pattern_lookback_days):]
    try:
        runtime_defaults = module.ExitSimulationConfig.from_runtime_defaults()
        _stats, trades, _shadow = module.analyze_paths(
            recent_paths,
            snapshot_dir=config.runtime_price_snapshots_dir,
            runtime_defaults=runtime_defaults,
        )
    except Exception:
        logger.warning("RecentPatternProfile backtest-analysis fallback failed", exc_info=True)
        return RecentPatternProfile.empty()

    rows = [
        {
            "date": trade.date,
            "ticker": trade.ticker,
            "headline": trade.headline,
            "keyword_hits": list(trade.keyword_hits),
            "news_category": trade.news_type,
            "hour_slot": trade.hour,
            "exit_ret_pct": trade.exit_pnl_pct,
        }
        for trade in trades
    ]
    return build_recent_pattern_profile_from_rows(rows, config)


def _persist_profile_summary(path: Path, profile: RecentPatternProfile) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(profile.summary(), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.warning("Failed to persist recent pattern profile to %s", path, exc_info=True)


def match_profit_boost(
    profile: RecentPatternProfile | None,
    *,
    news_type: str,
    ticker: str,
    hour_bucket: str,
) -> PatternCohort | None:
    if profile is None or not profile.enabled:
        return None
    for cohort in profile.boost_patterns:
        if cohort.matches(news_type=news_type, ticker=ticker, hour_bucket=hour_bucket):
            return cohort
    return None


def match_loss_guardrail(
    profile: RecentPatternProfile | None,
    *,
    news_type: str,
    ticker: str,
    hour_bucket: str,
) -> PatternCohort | None:
    if profile is None or not profile.enabled:
        return None
    for cohort in profile.loss_guardrail_patterns:
        if cohort.matches(news_type=news_type, ticker=ticker, hour_bucket=hour_bucket):
            return cohort
    return None
