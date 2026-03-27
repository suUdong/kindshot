"""Shared headline parsing helpers for analysis-time signal extraction."""

from __future__ import annotations

import re

_LEADING_TAG_RE = re.compile(r"^(?:\[[^\]]+\]\s*)+")
_BROKER_PREFIX_RE = re.compile(r"^(?:[A-Za-z가-힣&·]+증권)\s*[\"“”'‘’]\s*")
_WHITESPACE_RE = re.compile(r"\s+")
_QUOTE_CHARS_RE = re.compile(r"[\"“”'‘’]")

_CONTRACT_FAMILY_KEYWORDS = (
    "수주",
    "공급계약",
    "공급 계약",
    "납품계약",
    "단일판매",
    "단일판매ㆍ공급계약",
    "단일판매·공급계약",
)
_DIRECT_DISCLOSURE_MARKERS = (
    "체결",
    "체결공시",
    "계약체결",
    "결정",
    "공시",
    "규모",
    "매출액대비",
    "단일판매",
    "계약상대",
)
_ARTICLE_STYLE_MARKERS = (
    "[카드]",
    "[종합]",
    "[TOP's Pick]",
    "[클릭e종목]",
    "[클릭 e종목]",
    "[특징주]",
    "구조적 성장",
    "턴어라운드",
    "실적 가시성",
    "수요 확대 속",
    "요구 큰 폭 증가",
    "상승 여력",
    "전망",
    "기대",
    "기대감",
    "평가",
    "분석",
    "목표가",
    "목표주가",
    "상향",
    "하향",
    "본격화",
    "보인다",
    "강세",
)


def normalize_analysis_headline(headline: str) -> str:
    """Normalize headline for bucket/decision analysis while keeping raw logs intact."""
    text = str(headline or "").strip()
    if not text:
        return ""
    text = _LEADING_TAG_RE.sub("", text)
    text = _BROKER_PREFIX_RE.sub("", text)
    text = _QUOTE_CHARS_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip(" -:")


def is_broker_note_headline(headline: str, *, dorg: str = "") -> bool:
    raw = str(headline or "").strip()
    source = str(dorg or "").strip()
    if "증권" in source:
        return True
    if re.search(r"(?:^|[\s\[])?:?[A-Za-z가-힣&·]+증권\s*[\"“”'‘’]", raw):
        return True
    if "목표가" in raw or "목표주가" in raw:
        return True
    return bool(re.search(r"(?:상향|하향)\s*[-–]\s*[A-Za-z가-힣&·]+$", raw))


def is_commentary_headline(headline: str, *, dorg: str = "") -> bool:
    raw = str(headline or "").strip()
    normalized = normalize_analysis_headline(raw)
    if is_broker_note_headline(raw, dorg=dorg):
        return True
    return any(marker in raw or marker in normalized for marker in _ARTICLE_STYLE_MARKERS)


def is_contract_commentary_headline(headline: str, *, dorg: str = "") -> bool:
    raw = str(headline or "").strip()
    normalized = normalize_analysis_headline(raw)
    if not any(keyword in normalized for keyword in _CONTRACT_FAMILY_KEYWORDS):
        return False

    has_direct_disclosure_marker = any(marker in normalized for marker in _DIRECT_DISCLOSURE_MARKERS)
    has_amount = bool(re.search(r"\d", normalized))
    if has_direct_disclosure_marker and (has_amount or "단일판매" in normalized) and not is_broker_note_headline(raw, dorg=dorg):
        return False
    return is_commentary_headline(raw, dorg=dorg)
