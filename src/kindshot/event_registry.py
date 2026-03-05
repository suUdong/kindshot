"""Event ID generation, dedup, correction/withdrawal detection."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Optional

from kindshot.feed import RawDisclosure, _extract_kind_uid
from kindshot.models import (
    EventIdMethod,
    EventKind,
    ParentMatchMethod,
)


@dataclass
class ProcessedEvent:
    """Enriched event after registry processing."""

    event_id: str
    event_id_method: EventIdMethod
    event_kind: EventKind
    parent_id: Optional[str]
    event_group_id: str
    parent_match_method: Optional[ParentMatchMethod]
    parent_match_score: Optional[float]
    parent_candidate_count: Optional[int]
    kind_uid: Optional[str]
    raw: RawDisclosure


def _hash(*parts: str) -> str:
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def _normalize_title(title: str) -> str:
    """Remove correction markers and whitespace for comparison."""
    t = re.sub(r"\[정정\]", "", title)
    t = re.sub(r"정정\(취소\)", "", t)
    t = re.sub(r"정정", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _is_correction(title: str) -> bool:
    return "정정" in title or "[정정]" in title


def _is_withdrawal(title: str) -> bool:
    return "철회" in title or "취소" in title or "정정(취소)" in title


class EventRegistry:
    """Tracks seen events for dedup and links corrections to parents."""

    def __init__(self) -> None:
        self._seen_ids: set[str] = set()
        # ticker -> list of (event_id, normalized_title, detected_at)
        self._history: dict[str, list[tuple[str, str, datetime]]] = {}

    def process(self, raw: RawDisclosure) -> Optional[ProcessedEvent]:
        """Process a raw disclosure. Returns None if duplicate."""
        kind_uid = _extract_kind_uid(raw.link)

        # Generate event_id
        if kind_uid:
            event_id = _hash("KIND", kind_uid)
            method = EventIdMethod.UID
        else:
            # Fallback
            if raw.rss_guid:
                event_id = _hash("KIND", raw.rss_guid)
            elif raw.published:
                event_id = _hash("KIND", raw.published, raw.ticker, _normalize_title(raw.title))
            else:
                event_id = _hash("KIND", raw.detected_at.isoformat(), raw.ticker, _normalize_title(raw.title))
            method = EventIdMethod.FALLBACK

        # Dedup
        if event_id in self._seen_ids:
            return None
        self._seen_ids.add(event_id)

        # Determine event_kind
        if _is_withdrawal(raw.title):
            event_kind = EventKind.WITHDRAWAL
        elif _is_correction(raw.title):
            event_kind = EventKind.CORRECTION
        else:
            event_kind = EventKind.ORIGINAL

        # Correction parent linking
        parent_id: Optional[str] = None
        parent_match_method: Optional[ParentMatchMethod] = None
        parent_match_score: Optional[float] = None
        parent_candidate_count: Optional[int] = None
        norm_title = _normalize_title(raw.title)

        if event_kind in (EventKind.CORRECTION, EventKind.WITHDRAWAL):
            candidates = self._history.get(raw.ticker, [])
            parent_candidate_count = len(candidates)

            best_score = 0.0
            best_id: Optional[str] = None
            for cand_id, cand_title, _ts in candidates:
                # Exact match
                if cand_title == norm_title:
                    best_id = cand_id
                    best_score = 100.0
                    parent_match_method = ParentMatchMethod.EXACT_TITLE
                    break
                # Fuzzy match
                score = SequenceMatcher(None, norm_title, cand_title).ratio() * 100
                if score > best_score:
                    best_score = score
                    best_id = cand_id

            if best_id and best_score >= 60:
                parent_id = best_id
                parent_match_score = round(best_score, 1)
                if parent_match_method is None:
                    parent_match_method = ParentMatchMethod.FUZZY_TITLE
            else:
                parent_match_method = ParentMatchMethod.NONE
                parent_match_score = round(best_score, 1) if best_score > 0 else None

        event_group_id = parent_id if parent_id else event_id

        # Store in history
        self._history.setdefault(raw.ticker, []).append(
            (event_id, norm_title, raw.detected_at)
        )

        return ProcessedEvent(
            event_id=event_id,
            event_id_method=method,
            event_kind=event_kind,
            parent_id=parent_id,
            event_group_id=event_group_id,
            parent_match_method=parent_match_method,
            parent_match_score=parent_match_score,
            parent_candidate_count=parent_candidate_count,
            kind_uid=kind_uid,
            raw=raw,
        )
