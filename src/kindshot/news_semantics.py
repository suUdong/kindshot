"""Headline-level semantic enrichment for pipeline and decision surfaces."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from kindshot.headline_parser import (
    is_broker_note_headline,
    is_commentary_headline,
    is_direct_disclosure_headline,
    normalize_analysis_headline,
)
from kindshot.models import NewsClusterContext, NewsSignalContext
from kindshot.news_category import classify_news_type

_AMOUNT_FRAGMENT = r"(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>조원|조|억원|억)"
_CONTRACT_KEYWORDS = (
    "수주",
    "공급계약",
    "공급 계약",
    "납품계약",
    "단일판매",
    "단일판매ㆍ공급계약",
    "단일판매·공급계약",
)
_REVENUE_LABELS = ("매출액", "매출")
_OPERATING_PROFIT_LABELS = ("영업이익",)
_SALES_RATIO_RE = re.compile(r"(?:최근)?매출액대비\s*(\d[\d,]*(?:\.\d+)?)\s*%")
_TICKER_RE = re.compile(r"\(\d{6}\)")
_GENERIC_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:조원|조|억원|억|%)?")
_TOKEN_RE = re.compile(r"[A-Za-z가-힣]{2,}")
_STOPWORDS = frozenset(
    {
        "공시",
        "체결",
        "결정",
        "계약",
        "공급계약",
        "공급",
        "납품계약",
        "수주",
        "단일판매",
        "단일판매공급계약",
        "규모",
        "매출",
        "매출액",
        "영업이익",
        "최근",
        "지난해",
        "올해",
        "상반기",
        "하반기",
        "신규",
        "대형",
    }
)
_CATEGORY_BASE_SCORES: dict[str, int] = {
    "shareholder_return": 72,
    "clinical_regulatory": 70,
    "mna": 67,
    "contract": 61,
    "earnings_turnaround": 58,
    "product_technology": 53,
    "policy_funding": 49,
    "other": 50,
}


def _amount_to_eok(amount: str, unit: str) -> float:
    value = float(amount.replace(",", ""))
    return value * 10000.0 if unit.startswith("조") else value


def _extract_amount_near_labels(text: str, labels: tuple[str, ...]) -> float | None:
    label_pat = "|".join(re.escape(label) for label in labels)
    patterns = (
        re.compile(rf"(?:{label_pat})[^\d]{{0,12}}{_AMOUNT_FRAGMENT}"),
        re.compile(rf"{_AMOUNT_FRAGMENT}[^\d]{{0,12}}(?:{label_pat})"),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return _amount_to_eok(match.group("amount"), match.group("unit"))
    return None


def extract_contract_amount_eok(headline: str) -> float | None:
    text = normalize_analysis_headline(headline)
    if not any(keyword in text for keyword in _CONTRACT_KEYWORDS):
        return None
    match = re.search(_AMOUNT_FRAGMENT, text)
    if match:
        return _amount_to_eok(match.group("amount"), match.group("unit"))
    return None


def extract_revenue_eok(headline: str) -> float | None:
    return _extract_amount_near_labels(normalize_analysis_headline(headline), _REVENUE_LABELS)


def extract_operating_profit_eok(headline: str) -> float | None:
    return _extract_amount_near_labels(normalize_analysis_headline(headline), _OPERATING_PROFIT_LABELS)


def extract_sales_ratio_pct(headline: str) -> float | None:
    match = _SALES_RATIO_RE.search(normalize_analysis_headline(headline))
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _semantic_subject(*, headline: str, corp_name: str, news_category: str) -> str:
    text = normalize_analysis_headline(headline)
    if corp_name:
        text = text.replace(corp_name, " ")
    text = _TICKER_RE.sub(" ", text)
    text = _GENERIC_NUMBER_RE.sub(" ", text)
    tokens = []
    for token in _TOKEN_RE.findall(text):
        if token in _STOPWORDS:
            continue
        if token not in tokens:
            tokens.append(token)
    if not tokens:
        return news_category or "other"
    return " ".join(tokens[:4])


@dataclass
class _ClusterEntry:
    first_seen: datetime
    last_seen: datetime
    count: int = 1


class TickerNewsClusterTracker:
    """Lightweight in-memory cluster tracker for related ticker headlines."""

    def __init__(self, *, window_minutes: int = 120) -> None:
        self._window = timedelta(minutes=max(1, int(window_minutes)))
        self._clusters: dict[tuple[str, str, str], _ClusterEntry] = {}

    def _prune(self, now: datetime) -> None:
        expired = [
            key for key, entry in self._clusters.items()
            if now - entry.last_seen > self._window
        ]
        for key in expired:
            self._clusters.pop(key, None)

    def observe(
        self,
        *,
        ticker: str,
        corp_name: str,
        headline: str,
        news_category: str,
        detected_at: datetime,
    ) -> NewsClusterContext:
        self._prune(detected_at)
        subject = _semantic_subject(
            headline=headline,
            corp_name=corp_name,
            news_category=news_category,
        )
        key = (ticker, news_category or "other", subject)
        entry = self._clusters.get(key)
        if entry is None:
            entry = _ClusterEntry(first_seen=detected_at, last_seen=detected_at)
            self._clusters[key] = entry
        else:
            entry.count += 1
            entry.last_seen = detected_at
        cluster_id = hashlib.sha1("|".join(key).encode()).hexdigest()[:16]
        minutes_since_first = max(0, int((detected_at - entry.first_seen).total_seconds() // 60))
        return NewsClusterContext(
            cluster_id=cluster_id,
            cluster_key=subject,
            cluster_category=news_category or "other",
            cluster_size=entry.count,
            minutes_since_first=minutes_since_first,
            corroborated=entry.count >= 2,
        )


def compute_impact_score(signal: NewsSignalContext) -> tuple[int, list[str]]:
    category = signal.news_category or "other"
    score = _CATEGORY_BASE_SCORES.get(category, _CATEGORY_BASE_SCORES["other"])
    factors = [f"category:{category}"]

    if signal.direct_disclosure:
        score += 12
        factors.append("direct_disclosure")
    if signal.commentary:
        score -= 18
        factors.append("commentary_penalty")
    if signal.broker_note:
        score -= 12
        factors.append("broker_note_penalty")

    if signal.contract_amount_eok is not None:
        amount = signal.contract_amount_eok
        if amount >= 5000:
            score += 18
            factors.append("contract_5000eok_plus")
        elif amount >= 1000:
            score += 12
            factors.append("contract_1000eok_plus")
        elif amount >= 500:
            score += 8
            factors.append("contract_500eok_plus")
        elif amount >= 100:
            score += 4
            factors.append("contract_100eok_plus")
        else:
            score -= 8
            factors.append("contract_sub_100eok")

    if signal.revenue_eok is not None:
        if signal.revenue_eok >= 10000:
            score += 10
            factors.append("revenue_1jo_plus")
        elif signal.revenue_eok >= 1000:
            score += 6
            factors.append("revenue_1000eok_plus")

    if signal.operating_profit_eok is not None:
        if signal.operating_profit_eok >= 1000:
            score += 10
            factors.append("op_profit_1000eok_plus")
        elif signal.operating_profit_eok >= 100:
            score += 6
            factors.append("op_profit_100eok_plus")

    if signal.sales_ratio_pct is not None:
        ratio = signal.sales_ratio_pct
        if ratio >= 15:
            score += 15
            factors.append("sales_ratio_15pct_plus")
        elif ratio >= 10:
            score += 10
            factors.append("sales_ratio_10pct_plus")
        elif ratio >= 5:
            score += 5
            factors.append("sales_ratio_5pct_plus")
        elif ratio < 3:
            score -= 4
            factors.append("sales_ratio_sub_3pct")

    if signal.cluster is not None:
        if signal.cluster.cluster_size >= 3:
            score += 8
            factors.append("cluster_size_3_plus")
        elif signal.cluster.cluster_size == 2:
            score += 4
            factors.append("cluster_size_2")
        if signal.cluster.corroborated and signal.cluster.minutes_since_first <= 15:
            score += 2
            factors.append("fresh_cluster_corroboration")

    score = max(0, min(100, score))
    return score, factors


def apply_impact_score_confidence_adjustment(confidence: int, impact_score: int | None) -> int:
    if impact_score is None:
        return confidence
    if impact_score >= 88:
        delta = 4
    elif impact_score >= 80:
        delta = 2
    elif impact_score >= 70:
        delta = 1
    elif impact_score <= 30:
        delta = -6
    elif impact_score <= 45:
        delta = -3
    elif impact_score <= 55:
        delta = -1
    else:
        delta = 0
    return max(0, min(100, confidence + delta))


def build_news_signal(
    *,
    headline: str,
    ticker: str,
    corp_name: str,
    detected_at: datetime,
    dorg: str = "",
    keyword_hits: list[str] | None = None,
    cluster_tracker: TickerNewsClusterTracker | None = None,
) -> NewsSignalContext:
    analysis_headline = normalize_analysis_headline(headline)
    news_category = classify_news_type(analysis_headline, keyword_hits or [])
    cluster = (
        cluster_tracker.observe(
            ticker=ticker,
            corp_name=corp_name,
            headline=analysis_headline,
            news_category=news_category,
            detected_at=detected_at,
        )
        if cluster_tracker is not None
        else NewsClusterContext(
            cluster_id=hashlib.sha1(f"{ticker}|{news_category}|{analysis_headline}".encode()).hexdigest()[:16],
            cluster_key=_semantic_subject(
                headline=analysis_headline,
                corp_name=corp_name,
                news_category=news_category,
            ),
            cluster_category=news_category or "other",
            cluster_size=1,
            minutes_since_first=0,
            corroborated=False,
        )
    )
    signal = NewsSignalContext(
        analysis_headline=analysis_headline,
        news_category=news_category,
        direct_disclosure=is_direct_disclosure_headline(headline, dorg=dorg),
        commentary=is_commentary_headline(headline, dorg=dorg),
        broker_note=is_broker_note_headline(headline, dorg=dorg),
        contract_amount_eok=extract_contract_amount_eok(analysis_headline),
        revenue_eok=extract_revenue_eok(analysis_headline),
        operating_profit_eok=extract_operating_profit_eok(analysis_headline),
        sales_ratio_pct=extract_sales_ratio_pct(analysis_headline),
        cluster=cluster,
    )
    impact_score, factors = compute_impact_score(signal)
    signal.impact_score = impact_score
    signal.impact_factors = factors
    return signal
