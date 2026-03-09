"""Event ID generation, dedup, correction/withdrawal detection."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from kindshot.feed import RawDisclosure, _extract_kind_uid
from kindshot.models import (
    EventIdMethod,
    EventKind,
    ParentMatchMethod,
)

logger = logging.getLogger(__name__)


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
    """Tracks seen events for dedup and links corrections to parents.

    Optionally persists seen_ids to a JSONL file so restarts don't
    reprocess same-day events.
    """

    def __init__(self, state_dir: Optional[Path] = None) -> None:
        self._seen_ids: dict[str, datetime] = {}  # event_id -> detected_at
        # ticker -> list of (event_id, normalized_title, detected_at, event_kind)
        self._history: dict[str, list[tuple[str, str, datetime, EventKind]]] = {}
        self._current_date: Optional[str] = None  # YYYYMMDD for TTL
        self._state_dir = state_dir
        if state_dir:
            state_dir.mkdir(parents=True, exist_ok=True)
            self._load_state()

    def _state_file(self) -> Optional[Path]:
        if not self._state_dir or not self._current_date:
            return None
        return self._state_dir / f"dedup_{self._current_date}.jsonl"

    def _load_state(self) -> None:
        """Load seen_ids from today's state file if it exists."""
        kst = timezone(timedelta(hours=9))
        today = datetime.now(kst).strftime("%Y%m%d")
        self._current_date = today
        state_file = self._state_file()
        if not state_file or not state_file.exists():
            return
        try:
            with open(state_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    eid = rec.get("event_id", "")
                    ts = rec.get("detected_at", "")
                    if eid:
                        self._seen_ids[eid] = datetime.fromisoformat(ts) if ts else datetime.now(kst)
            logger.info("Loaded %d dedup entries from %s", len(self._seen_ids), state_file.name)
        except Exception:
            logger.exception("Failed to load dedup state from %s", state_file)

    def _persist_id(self, event_id: str, detected_at: datetime) -> None:
        """Append a single event_id to today's state file."""
        state_file = self._state_file()
        if not state_file:
            return
        try:
            with open(state_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({"event_id": event_id, "detected_at": detected_at.isoformat()}) + "\n")
        except Exception:
            logger.exception("Failed to persist dedup id %s", event_id)

    def _prune_if_new_day(self, now: datetime) -> None:
        """Clear history when KST date changes (TTL = current trading day)."""
        kst = timezone(timedelta(hours=9))
        today = now.astimezone(kst).strftime("%Y%m%d")
        if self._current_date is not None and self._current_date != today:
            self._seen_ids.clear()
            self._history.clear()
        self._current_date = today

    def process(self, raw: RawDisclosure) -> Optional[ProcessedEvent]:
        """Process a raw disclosure. Returns None if duplicate."""
        self._prune_if_new_day(raw.detected_at)
        kind_uid = _extract_kind_uid(raw.link)

        # Generate event_id
        if kind_uid:
            event_id = _hash("KIND", kind_uid)
            method = EventIdMethod.UID
        else:
            # Fallback: include link for collision resistance
            if raw.rss_guid:
                event_id = _hash("KIND", raw.rss_guid)
            elif raw.published:
                event_id = _hash("KIND", raw.published, raw.ticker, _normalize_title(raw.title), raw.link)
            else:
                event_id = _hash("KIND", raw.detected_at.isoformat(), raw.ticker, _normalize_title(raw.title), raw.link)
            method = EventIdMethod.FALLBACK

        # Dedup
        if event_id in self._seen_ids:
            return None
        self._seen_ids[event_id] = raw.detected_at
        self._persist_id(event_id, raw.detected_at)

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
            all_entries = self._history.get(raw.ticker, [])
            # Only ORIGINAL events are valid parent candidates
            candidates = [(eid, t, ts) for eid, t, ts, ek in all_entries if ek == EventKind.ORIGINAL]
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

        # Store in history (cap at 100 per ticker to bound memory/fuzzy matching)
        history = self._history.setdefault(raw.ticker, [])
        history.append((event_id, norm_title, raw.detected_at, event_kind))
        if len(history) > 100:
            self._history[raw.ticker] = history[-100:]

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
