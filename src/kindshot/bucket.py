"""Keyword-based 5-bucket classification with NEG-first override."""

from __future__ import annotations

from dataclasses import dataclass, field
from kindshot.models import Bucket


# ── Keyword lists ──────────────────────────────────────

NEG_STRONG_KEYWORDS: list[str] = [
    "유증", "유상증자",
    "CB발행", "CB 발행", "전환사채",
    "전환가 조정", "전환가조정",
    "대주주 매각", "대주주매각",
    "블록딜",
    "소송",
    "규제",
    "해지",
    "철회",
    "취소",
]

POS_STRONG_KEYWORDS: list[str] = [
    "수주",
    "공급계약",
    "실적 상향", "실적상향",
    "자사주 매입", "자사주매입", "자기주식 취득", "자기주식취득",
    "신규사업", "신규 사업",
    "합작",
    "대형 계약", "대형계약",
    "인수",
    "지분 취득", "지분취득",
    "특허",
    "매출 확대", "매출확대",
    "투자유치",
    "MOU", "업무협약",
]

POS_WEAK_KEYWORDS: list[str] = [
    "리포트",
    "전망",
    "테마",
]

NEG_WEAK_KEYWORDS: list[str] = [
    "루머",
    "풍문",
]


@dataclass
class BucketResult:
    bucket: Bucket
    keyword_hits: list[str] = field(default_factory=list)
    matched_positions: list[tuple[str, int]] = field(default_factory=list)


def _find_keywords(text: str, keywords: list[str]) -> list[tuple[str, int]]:
    """Find all keyword matches with their positions."""
    matches: list[tuple[str, int]] = []
    for kw in keywords:
        idx = text.find(kw)
        if idx >= 0:
            matches.append((kw, idx))
    return matches


def classify(headline: str) -> BucketResult:
    """Classify headline into one of 5 buckets. NEG-first override."""
    text = headline

    # Priority 1: NEG_STRONG
    neg_strong = _find_keywords(text, NEG_STRONG_KEYWORDS)
    if neg_strong:
        return BucketResult(
            bucket=Bucket.NEG_STRONG,
            keyword_hits=[kw for kw, _ in neg_strong],
            matched_positions=neg_strong,
        )

    # Priority 2: POS_STRONG
    pos_strong = _find_keywords(text, POS_STRONG_KEYWORDS)
    if pos_strong:
        return BucketResult(
            bucket=Bucket.POS_STRONG,
            keyword_hits=[kw for kw, _ in pos_strong],
            matched_positions=pos_strong,
        )

    # Priority 3: POS_WEAK
    pos_weak = _find_keywords(text, POS_WEAK_KEYWORDS)
    if pos_weak:
        return BucketResult(
            bucket=Bucket.POS_WEAK,
            keyword_hits=[kw for kw, _ in pos_weak],
            matched_positions=pos_weak,
        )

    # Priority 4: NEG_WEAK
    neg_weak = _find_keywords(text, NEG_WEAK_KEYWORDS)
    if neg_weak:
        return BucketResult(
            bucket=Bucket.NEG_WEAK,
            keyword_hits=[kw for kw, _ in neg_weak],
            matched_positions=neg_weak,
        )

    # Priority 5: UNKNOWN
    return BucketResult(bucket=Bucket.UNKNOWN)
