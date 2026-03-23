"""Keyword-based 6-bucket classification with NEG-first override.

Bucket priority: IGNORE_OVERRIDE > NEG_STRONG > POS_STRONG > NEG_WEAK > POS_WEAK > IGNORE > UNKNOWN
Longer (compound) keywords are matched before shorter ones within each list.

Keywords are loaded from keywords/buckets.json — edit that file to add/remove keywords
without changing code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from kindshot.models import Bucket

_KEYWORDS_DIR = Path(__file__).parent / "keywords"
_KEYWORDS_CACHE: dict[str, list[str]] | None = None


def _load_keywords() -> dict[str, list[str]]:
    global _KEYWORDS_CACHE
    if _KEYWORDS_CACHE is None:
        path = _KEYWORDS_DIR / "buckets.json"
        _KEYWORDS_CACHE = json.loads(path.read_text(encoding="utf-8"))
    return _KEYWORDS_CACHE


def _get_keywords(bucket_key: str) -> list[str]:
    return _load_keywords().get(bucket_key, [])


# Public accessors for backward compatibility (used by unknown_review.py etc.)
def _lazy_list(key: str) -> list[str]:
    return _get_keywords(key)


class _KeywordList:
    """Lazy-loading list proxy for keyword access."""
    def __init__(self, key: str) -> None:
        self._key = key
        self._loaded: list[str] | None = None

    def _ensure(self) -> list[str]:
        if self._loaded is None:
            self._loaded = _get_keywords(self._key)
        return self._loaded

    def __iter__(self):
        return iter(self._ensure())

    def __contains__(self, item):
        return item in self._ensure()

    def __len__(self):
        return len(self._ensure())

    def __getitem__(self, idx):
        return self._ensure()[idx]

    def __add__(self, other):
        return list(self._ensure()) + list(other)

    def __radd__(self, other):
        return list(other) + list(self._ensure())

    def __repr__(self):
        return f"_KeywordList({self._key!r}, len={len(self)})"


IGNORE_KEYWORDS: list[str] = _KeywordList("IGNORE")  # type: ignore[assignment]
IGNORE_OVERRIDE_KEYWORDS: list[str] = _KeywordList("IGNORE_OVERRIDE")  # type: ignore[assignment]
NEG_STRONG_KEYWORDS: list[str] = _KeywordList("NEG_STRONG")  # type: ignore[assignment]
NEG_WEAK_KEYWORDS: list[str] = _KeywordList("NEG_WEAK")  # type: ignore[assignment]
POS_STRONG_KEYWORDS: list[str] = _KeywordList("POS_STRONG")  # type: ignore[assignment]
POS_WEAK_KEYWORDS: list[str] = _KeywordList("POS_WEAK")  # type: ignore[assignment]


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
    """Classify headline into one of 6 buckets.

    Priority: IGNORE_OVERRIDE > NEG_STRONG > POS_STRONG > NEG_WEAK > POS_WEAK > IGNORE > UNKNOWN
    """
    text = headline

    ignore_override = _find_keywords(text, IGNORE_OVERRIDE_KEYWORDS)
    if ignore_override:
        return BucketResult(
            bucket=Bucket.IGNORE,
            keyword_hits=[kw for kw, _ in ignore_override],
            matched_positions=ignore_override,
        )

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

    # Priority 3: NEG_WEAK
    neg_weak = _find_keywords(text, NEG_WEAK_KEYWORDS)
    if neg_weak:
        return BucketResult(
            bucket=Bucket.NEG_WEAK,
            keyword_hits=[kw for kw, _ in neg_weak],
            matched_positions=neg_weak,
        )

    # Priority 4: POS_WEAK
    pos_weak = _find_keywords(text, POS_WEAK_KEYWORDS)
    if pos_weak:
        return BucketResult(
            bucket=Bucket.POS_WEAK,
            keyword_hits=[kw for kw, _ in pos_weak],
            matched_positions=pos_weak,
        )

    # Priority 5: IGNORE (노이즈 — NEG/POS에 안 걸린 것만)
    ignore = _find_keywords(text, IGNORE_KEYWORDS)
    if ignore:
        return BucketResult(
            bucket=Bucket.IGNORE,
            keyword_hits=[kw for kw, _ in ignore],
            matched_positions=ignore,
        )

    # Priority 6: UNKNOWN
    return BucketResult(bucket=Bucket.UNKNOWN)
