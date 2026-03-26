"""UNKNOWN shadow-review logging and LLM review helpers."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, replace
import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp

from kindshot.bucket import (
    IGNORE_KEYWORDS,
    IGNORE_OVERRIDE_KEYWORDS,
    NEG_STRONG_KEYWORDS,
    NEG_WEAK_KEYWORDS,
    POS_STRONG_KEYWORDS,
    POS_WEAK_KEYWORDS,
)
from kindshot.config import Config
from kindshot.llm_client import LlmClient
from kindshot.models import (
    Bucket,
    PromotionStatus,
    ReviewPolarity,
    ReviewStatus,
    UnknownInboxRecord,
    UnknownPromotionRecord,
    UnknownReviewRecord,
)

from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UnknownReviewRequest:
    event_id: str
    detected_at: datetime
    runtime_mode: str
    ticker: str
    corp_name: str
    headline: str
    rss_link: str
    rss_guid: Optional[str]
    published: Optional[str]
    source: str
    article_text: str = ""
    article_source: str = ""


@dataclass(frozen=True)
class ArticleEnrichmentResult:
    status: str
    article_text: str = ""
    body_source: str = ""


ALLOWED_PROMOTION_BUCKETS = frozenset({Bucket.POS_STRONG, Bucket.NEG_STRONG})
_PATCHABLE_BUCKET_KEYWORD_LISTS = {
    Bucket.IGNORE.value: "IGNORE_KEYWORDS",
    Bucket.NEG_STRONG.value: "NEG_STRONG_KEYWORDS",
    Bucket.NEG_WEAK.value: "NEG_WEAK_KEYWORDS",
    Bucket.POS_STRONG.value: "POS_STRONG_KEYWORDS",
    Bucket.POS_WEAK.value: "POS_WEAK_KEYWORDS",
}


def _daily_log_path(base_dir: Path, ts: datetime) -> Path:
    return base_dir / f"{ts.astimezone(_KST).strftime('%Y-%m-%d')}.jsonl"


def _ops_output_path(config: Config, explicit_path: str = "") -> Path:
    if explicit_path:
        return Path(explicit_path)
    return config.unknown_review_ops_summary_path


def _rule_report_output_path(config: Config, explicit_path: str = "") -> Path:
    if explicit_path:
        return Path(explicit_path)
    return config.unknown_review_rule_report_path


def _rule_queue_output_path(config: Config, explicit_path: str = "") -> Path:
    if explicit_path:
        return Path(explicit_path)
    return config.unknown_review_rule_queue_path


def _rule_patch_output_path(config: Config, explicit_path: str = "") -> Path:
    if explicit_path:
        return Path(explicit_path)
    return config.unknown_review_rule_patch_path


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def append_unknown_inbox(config: Config, request: UnknownReviewRequest) -> None:
    record = UnknownInboxRecord(
        event_id=request.event_id,
        detected_at=request.detected_at,
        runtime_mode=request.runtime_mode,
        ticker=request.ticker,
        corp_name=request.corp_name,
        headline=request.headline,
        rss_link=request.rss_link,
        source=request.source,
    )
    _append_jsonl(_daily_log_path(config.unknown_inbox_dir, request.detected_at), record.model_dump(mode="json"))


def append_unknown_review(config: Config, detected_at: datetime, record: UnknownReviewRecord) -> None:
    _append_jsonl(_daily_log_path(config.unknown_review_dir, detected_at), record.model_dump(mode="json"))


def append_unknown_promotion(config: Config, detected_at: datetime, record: UnknownPromotionRecord) -> None:
    _append_jsonl(_daily_log_path(config.unknown_promotion_dir, detected_at), record.model_dump(mode="json"))


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _available_unknown_dates(config: Config) -> list[str]:
    dates: set[str] = set()
    for base_dir in (config.unknown_inbox_dir, config.unknown_review_dir, config.unknown_promotion_dir):
        if not base_dir.exists():
            continue
        for path in base_dir.glob("*.jsonl"):
            dates.add(path.stem)
    return sorted(dates, reverse=True)


def _top_counts(counter: Counter[str], *, limit: int = 3) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _normalize_article_text(text: str, *, limit: int) -> str:
    cleaned = html.unescape(re.sub(r"\s+", " ", text or "")).strip()
    return cleaned[: max(0, limit)].strip()


def _extract_article_text_from_html(body: str, *, limit: int) -> tuple[str, str]:
    html_body = body or ""
    patterns = (
        ("article", r"<article\b[^>]*>(.*?)</article>"),
        ("main", r"<main\b[^>]*>(.*?)</main>"),
        ("content", r"""<(div|section)\b[^>]*(?:id|class)=["'][^"']*(?:content|article|news|view)[^"']*["'][^>]*>(.*?)</\1>"""),
    )
    for source, pattern in patterns:
        match = re.search(pattern, html_body, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        candidate_html = match.group(match.lastindex or 1)
        candidate_text = _normalize_article_text(re.sub(r"<[^>]+>", " ", candidate_html), limit=limit)
        if candidate_text:
            return candidate_text, f"rss_link_html:{source}"

    meta_patterns = (
        ("og:description", r"""<meta\b[^>]*property=["']og:description["'][^>]*content=["']([^"']+)["'][^>]*>"""),
        ("description", r"""<meta\b[^>]*name=["']description["'][^>]*content=["']([^"']+)["'][^>]*>"""),
    )
    for source, pattern in meta_patterns:
        match = re.search(pattern, html_body, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        candidate_text = _normalize_article_text(match.group(1), limit=limit)
        if candidate_text:
            return candidate_text, f"rss_link_meta:{source}"

    stripped = _normalize_article_text(re.sub(r"<[^>]+>", " ", html_body), limit=limit)
    if stripped:
        return stripped, "rss_link_html:full_page"
    return "", ""


def _build_unknown_review_day_summary(config: Config, dt: str) -> dict[str, Any]:
    inbox_rows = _read_jsonl(config.unknown_inbox_dir / f"{dt}.jsonl")
    review_rows = _read_jsonl(config.unknown_review_dir / f"{dt}.jsonl")
    promotion_rows = _read_jsonl(config.unknown_promotion_dir / f"{dt}.jsonl")

    latest_review_by_event: dict[str, dict[str, Any]] = {}
    latest_promotion_by_event: dict[str, dict[str, Any]] = {}
    for row in review_rows:
        event_id = str(row.get("event_id", "")).strip()
        if event_id:
            latest_review_by_event[event_id] = row
    for row in promotion_rows:
        event_id = str(row.get("event_id", "")).strip()
        if event_id:
            latest_promotion_by_event[event_id] = row

    review_status_counts: Counter[str] = Counter()
    suggested_bucket_counts: Counter[str] = Counter()
    gate_reason_counts: Counter[str] = Counter()
    promotion_status_counts: Counter[str] = Counter()
    needs_article_body_count = 0
    article_enriched_review_count = 0
    article_fetch_success_count = 0

    for row in latest_review_by_event.values():
        status = str(row.get("review_status", "")).strip()
        if status:
            review_status_counts[status] += 1
        bucket = str(row.get("suggested_bucket", "")).strip()
        if bucket:
            suggested_bucket_counts[bucket] += 1
        if bool(row.get("needs_article_body", False)):
            needs_article_body_count += 1
        if str(row.get("review_iteration", "")).strip() == "article_enriched":
            article_enriched_review_count += 1
        if str(row.get("body_fetch_status", "")).strip() == "fetched":
            article_fetch_success_count += 1

    for row in latest_promotion_by_event.values():
        status = str(row.get("promotion_status", "")).strip()
        if status:
            promotion_status_counts[status] += 1
        for reason in row.get("gate_reasons", []) or []:
            gate_reason_counts[str(reason)] += 1

    inbox_ids = {
        str(row.get("event_id", "")).strip()
        for row in inbox_rows
        if str(row.get("event_id", "")).strip()
    }
    pending_review_count = len(inbox_ids - set(latest_review_by_event.keys()))

    if not inbox_rows and not review_rows and not promotion_rows:
        health = "empty"
    elif promotion_status_counts.get(PromotionStatus.ERROR.value, 0) > 0:
        health = "promotion_errors"
    elif review_status_counts.get(ReviewStatus.ERROR.value, 0) > 0:
        health = "review_errors"
    elif pending_review_count > 0:
        health = "review_backlog"
    else:
        health = "healthy"

    return {
        "date": dt,
        "inbox_count": len(inbox_rows),
        "review_count": len(review_rows),
        "review_ok_count": review_status_counts.get(ReviewStatus.OK.value, 0),
        "review_error_count": review_status_counts.get(ReviewStatus.ERROR.value, 0),
        "review_skipped_count": review_status_counts.get(ReviewStatus.SKIPPED.value, 0),
        "promotion_promoted_count": promotion_status_counts.get(PromotionStatus.PROMOTED.value, 0),
        "promotion_rejected_count": promotion_status_counts.get(PromotionStatus.REJECTED.value, 0),
        "promotion_error_count": promotion_status_counts.get(PromotionStatus.ERROR.value, 0),
        "pending_review_count": pending_review_count,
        "needs_article_body_count": needs_article_body_count,
        "article_enriched_review_count": article_enriched_review_count,
        "article_fetch_success_count": article_fetch_success_count,
        "top_suggested_buckets": _top_counts(suggested_bucket_counts),
        "top_gate_reasons": _top_counts(gate_reason_counts),
        "health": health,
    }


def _print_unknown_review_ops_summary(report: dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("UNKNOWN REVIEW OPS SUMMARY")
    print("=" * 60)
    print(
        "Dates=%d inbox=%d reviews=%d promoted=%d pending=%d health=%s"
        % (
            report.get("date_count", 0),
            report.get("inbox_count", 0),
            report.get("review_count", 0),
            report.get("promotion_promoted_count", 0),
            report.get("pending_review_count", 0),
            ",".join(f"{k}:{v}" for k, v in sorted(report.get("health_counts", {}).items())) or "-",
        )
    )
    for row in report.get("rows", []):
        print(
            "%s health=%s inbox=%d review_ok=%d review_err=%d review_skip=%d promoted=%d rejected=%d pending=%d needs_body=%d enriched=%d fetch_ok=%d"
            % (
                row["date"],
                row["health"],
                row["inbox_count"],
                row["review_ok_count"],
                row["review_error_count"],
                row["review_skipped_count"],
                row["promotion_promoted_count"],
                row["promotion_rejected_count"],
                row["pending_review_count"],
                row["needs_article_body_count"],
                row["article_enriched_review_count"],
                row["article_fetch_success_count"],
            )
        )
    print("=" * 60)


def unknown_review_ops_summary(config: Config, *, limit: int = 10, output_path: str = "") -> dict[str, Any]:
    dates = _available_unknown_dates(config)
    rows = [_build_unknown_review_day_summary(config, dt) for dt in dates]
    health_counts: Counter[str] = Counter()
    inbox_count = 0
    review_count = 0
    promoted_count = 0
    pending_count = 0
    for row in rows:
        health_counts[str(row.get("health", "empty"))] += 1
        inbox_count += int(row.get("inbox_count", 0))
        review_count += int(row.get("review_count", 0))
        promoted_count += int(row.get("promotion_promoted_count", 0))
        pending_count += int(row.get("pending_review_count", 0))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date_count": len(rows),
        "inbox_count": inbox_count,
        "review_count": review_count,
        "promotion_promoted_count": promoted_count,
        "pending_review_count": pending_count,
        "health_counts": dict(health_counts),
        "all_rows": rows,
        "rows": rows[: max(1, limit)],
    }
    _print_unknown_review_ops_summary(report)
    _write_report(_ops_output_path(config, output_path), report)
    return report


def _latest_rows_by_event(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        event_id = str(row.get("event_id", "")).strip()
        if event_id:
            latest[event_id] = row
    return latest


def _candidate_keys(review_row: dict[str, Any]) -> list[str]:
    candidates = [
        str(item).strip()
        for item in (review_row.get("keyword_candidates") or [])
        if str(item).strip()
    ]
    if candidates:
        return candidates
    canonical = str(review_row.get("canonical_headline", "")).strip()
    return [canonical] if canonical else []


def _build_unknown_rule_day_report(config: Config, dt: str) -> dict[str, Any]:
    review_rows = _read_jsonl(config.unknown_review_dir / f"{dt}.jsonl")
    promotion_rows = _read_jsonl(config.unknown_promotion_dir / f"{dt}.jsonl")
    latest_reviews = _latest_rows_by_event(review_rows)
    latest_promotions = _latest_rows_by_event(promotion_rows)

    candidates: dict[str, dict[str, Any]] = {}
    for event_id, review_row in latest_reviews.items():
        if str(review_row.get("review_status", "")).strip() != ReviewStatus.OK.value:
            continue
        candidate_keys = _candidate_keys(review_row)
        if not candidate_keys:
            continue
        promotion_row = latest_promotions.get(event_id, {})
        promotion_status = str(promotion_row.get("promotion_status", "")).strip()
        suggested_bucket = str(review_row.get("suggested_bucket", "")).strip()
        for candidate in candidate_keys:
            entry = candidates.setdefault(
                candidate,
                {
                    "candidate": candidate,
                    "canonical_headline_examples": [],
                    "suggested_bucket_counts": Counter(),
                    "review_ok_count": 0,
                    "promotion_promoted_count": 0,
                    "promotion_rejected_count": 0,
                    "needs_article_body_count": 0,
                    "article_enriched_review_count": 0,
                    "reason_code_counts": Counter(),
                    "risk_flag_counts": Counter(),
                    "sample_headlines": [],
                    "sample_event_ids": [],
                },
            )
            entry["review_ok_count"] += 1
            if suggested_bucket:
                entry["suggested_bucket_counts"][suggested_bucket] += 1
            if promotion_status == PromotionStatus.PROMOTED.value:
                entry["promotion_promoted_count"] += 1
            elif promotion_status == PromotionStatus.REJECTED.value:
                entry["promotion_rejected_count"] += 1
            if bool(review_row.get("needs_article_body", False)):
                entry["needs_article_body_count"] += 1
            if str(review_row.get("review_iteration", "")).strip() == "article_enriched":
                entry["article_enriched_review_count"] += 1
            canonical = str(review_row.get("canonical_headline", "")).strip()
            if canonical and canonical not in entry["canonical_headline_examples"] and len(entry["canonical_headline_examples"]) < 3:
                entry["canonical_headline_examples"].append(canonical)
            for code in review_row.get("reason_codes", []) or []:
                entry["reason_code_counts"][str(code)] += 1
            for flag in review_row.get("risk_flags", []) or []:
                entry["risk_flag_counts"][str(flag)] += 1
            headline = str(review_row.get("canonical_headline", "")).strip() or str(review_row.get("reason", "")).strip()
            if not headline:
                headline = candidate
            if headline not in entry["sample_headlines"] and len(entry["sample_headlines"]) < 3:
                entry["sample_headlines"].append(headline)
            if event_id not in entry["sample_event_ids"] and len(entry["sample_event_ids"]) < 5:
                entry["sample_event_ids"].append(event_id)

    rows: list[dict[str, Any]] = []
    promoted_candidate_count = 0
    needs_article_body_candidate_count = 0
    for candidate, entry in sorted(
        candidates.items(),
        key=lambda item: (
            -int(item[1]["promotion_promoted_count"]),
            -int(item[1]["review_ok_count"]),
            item[0],
        ),
    ):
        if entry["promotion_promoted_count"] > 0:
            promoted_candidate_count += 1
        if entry["needs_article_body_count"] > 0:
            needs_article_body_candidate_count += 1
        rows.append(
            {
                "candidate": candidate,
                "canonical_headline_examples": entry["canonical_headline_examples"],
                "suggested_bucket_counts": dict(entry["suggested_bucket_counts"]),
                "review_ok_count": entry["review_ok_count"],
                "promotion_promoted_count": entry["promotion_promoted_count"],
                "promotion_rejected_count": entry["promotion_rejected_count"],
                "needs_article_body_count": entry["needs_article_body_count"],
                "article_enriched_review_count": entry["article_enriched_review_count"],
                "top_reason_codes": _top_counts(entry["reason_code_counts"]),
                "top_risk_flags": _top_counts(entry["risk_flag_counts"]),
                "sample_headlines": entry["sample_headlines"],
                "sample_event_ids": entry["sample_event_ids"],
            }
        )

    return {
        "date": dt,
        "candidate_count": len(rows),
        "promoted_candidate_count": promoted_candidate_count,
        "needs_article_body_candidate_count": needs_article_body_candidate_count,
        "top_candidates": rows[:5],
        "all_candidates": rows,
    }


def _print_unknown_rule_report(report: dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("UNKNOWN REVIEW RULE REPORT")
    print("=" * 60)
    print(
        "Dates=%d candidates=%d promoted_candidates=%d needs_body_candidates=%d"
        % (
            report.get("date_count", 0),
            report.get("candidate_count", 0),
            report.get("promoted_candidate_count", 0),
            report.get("needs_article_body_candidate_count", 0),
        )
    )
    for row in report.get("rows", []):
        top = row.get("top_candidates", [])
        top_label = top[0]["candidate"] if top else "-"
        print(
            "%s candidates=%d promoted_candidates=%d needs_body_candidates=%d top=%s"
            % (
                row["date"],
                row["candidate_count"],
                row["promoted_candidate_count"],
                row["needs_article_body_candidate_count"],
                top_label,
            )
        )
    print("=" * 60)


def _build_unknown_rule_report(config: Config) -> dict[str, Any]:
    dates = _available_unknown_dates(config)
    rows = [_build_unknown_rule_day_report(config, dt) for dt in dates]
    candidate_count = sum(int(row.get("candidate_count", 0)) for row in rows)
    promoted_candidate_count = sum(int(row.get("promoted_candidate_count", 0)) for row in rows)
    needs_article_body_candidate_count = sum(int(row.get("needs_article_body_candidate_count", 0)) for row in rows)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date_count": len(rows),
        "candidate_count": candidate_count,
        "promoted_candidate_count": promoted_candidate_count,
        "needs_article_body_candidate_count": needs_article_body_candidate_count,
        "all_rows": rows,
    }


def unknown_review_rule_report(config: Config, *, limit: int = 10, output_path: str = "") -> dict[str, Any]:
    report = _build_unknown_rule_report(config)
    report["rows"] = report["all_rows"][: max(1, limit)]
    _print_unknown_rule_report(report)
    _write_report(_rule_report_output_path(config, output_path), report)
    return report


def _existing_keyword_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for bucket, keywords in (
        (Bucket.IGNORE.value, IGNORE_KEYWORDS + IGNORE_OVERRIDE_KEYWORDS),
        (Bucket.NEG_STRONG.value, NEG_STRONG_KEYWORDS),
        (Bucket.NEG_WEAK.value, NEG_WEAK_KEYWORDS),
        (Bucket.POS_STRONG.value, POS_STRONG_KEYWORDS),
        (Bucket.POS_WEAK.value, POS_WEAK_KEYWORDS),
    ):
        for keyword in keywords:
            normalized = keyword.strip()
            if normalized:
                mapping.setdefault(normalized, bucket)
    return mapping


def _recommended_bucket(row: dict[str, Any]) -> str:
    counts = row.get("suggested_bucket_counts", {}) or {}
    if not counts:
        return Bucket.UNKNOWN.value
    return sorted(counts.items(), key=lambda item: (-int(item[1]), item[0]))[0][0]


def _selection_reason(row: dict[str, Any], *, config: Config, existing_bucket: str) -> str:
    if existing_bucket:
        return "already_exists"
    if int(row.get("review_ok_count", 0)) < max(1, config.unknown_rule_queue_min_reviews):
        return "review_count_below_min"
    if int(row.get("promotion_promoted_count", 0)) < max(0, config.unknown_rule_queue_min_promoted):
        return "promotion_count_below_min"
    if int(row.get("needs_article_body_count", 0)) > 0:
        return "needs_article_body"
    return "selected"


def _print_unknown_rule_queue(report: dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("UNKNOWN REVIEW RULE QUEUE")
    print("=" * 60)
    print(
        "Candidates=%d selected=%d existing=%d"
        % (
            report.get("candidate_count", 0),
            report.get("selected_count", 0),
            report.get("already_exists_count", 0),
        )
    )
    for row in report.get("rows", []):
        print(
            "%s bucket=%s reviews=%d promoted=%d needs_body=%d reason=%s"
            % (
                row["candidate"],
                row["recommended_bucket"],
                row["review_ok_count"],
                row["promotion_promoted_count"],
                row["needs_article_body_count"],
                row["selection_reason"],
            )
        )
    print("=" * 60)


def unknown_review_rule_queue(config: Config, *, limit: int = 10, output_path: str = "") -> dict[str, Any]:
    report = _build_unknown_rule_report(config)
    existing_keywords = _existing_keyword_map()
    selected_rows: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    selection_reason_counts: Counter[str] = Counter()
    already_exists_count = 0

    for date_row in report.get("all_rows", []):
        for candidate_row in date_row.get("all_candidates", []):
            candidate = str(candidate_row.get("candidate", "")).strip()
            existing_bucket = existing_keywords.get(candidate, "")
            selection_reason = _selection_reason(candidate_row, config=config, existing_bucket=existing_bucket)
            row = {
                "date": date_row.get("date", ""),
                "candidate": candidate,
                "recommended_bucket": _recommended_bucket(candidate_row),
                "review_ok_count": int(candidate_row.get("review_ok_count", 0)),
                "promotion_promoted_count": int(candidate_row.get("promotion_promoted_count", 0)),
                "promotion_rejected_count": int(candidate_row.get("promotion_rejected_count", 0)),
                "needs_article_body_count": int(candidate_row.get("needs_article_body_count", 0)),
                "article_enriched_review_count": int(candidate_row.get("article_enriched_review_count", 0)),
                "existing_keyword_bucket": existing_bucket,
                "selection_reason": selection_reason,
                "canonical_headline_examples": candidate_row.get("canonical_headline_examples", []),
                "sample_event_ids": candidate_row.get("sample_event_ids", []),
            }
            all_rows.append(row)
            selection_reason_counts[selection_reason] += 1
            if selection_reason == "already_exists":
                already_exists_count += 1
            if selection_reason == "selected":
                selected_rows.append(row)

    selected_rows.sort(
        key=lambda row: (
            -int(row.get("promotion_promoted_count", 0)),
            -int(row.get("review_ok_count", 0)),
            row.get("candidate", ""),
        )
    )
    report_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(all_rows),
        "selected_count": len(selected_rows),
        "already_exists_count": already_exists_count,
        "selection_reason_counts": dict(selection_reason_counts),
        "all_rows": all_rows,
        "rows": selected_rows[: max(1, limit)],
    }
    _print_unknown_rule_queue(report_payload)
    _write_report(_rule_queue_output_path(config, output_path), report_payload)
    return report_payload


def _merge_unique_strings(existing: list[str], values: list[str], *, limit: int) -> list[str]:
    merged = list(existing)
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in merged:
            continue
        merged.append(normalized)
        if len(merged) >= limit:
            break
    return merged


def _print_unknown_rule_patch(report: dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("UNKNOWN REVIEW RULE PATCH")
    print("=" * 60)
    print(
        "Source=%s candidates=%d selected=%d patch_buckets=%d"
        % (
            report.get("source_queue_path", "-"),
            report.get("candidate_count", 0),
            report.get("selected_count", 0),
            report.get("patch_bucket_count", 0),
        )
    )
    for bucket_patch in report.get("bucket_patches", []):
        print(
            "%s -> %s keywords=%d"
            % (
                bucket_patch["bucket"],
                bucket_patch["target_keyword_list"],
                len(bucket_patch.get("keywords", [])),
            )
        )
        for keyword in bucket_patch.get("keywords", [])[:5]:
            print(f"  - {keyword}")
    print("=" * 60)


def unknown_review_rule_patch(config: Config, *, limit: int = 20, output_path: str = "") -> dict[str, Any]:
    queue_path = config.unknown_review_rule_queue_path
    queue_report = _read_json(queue_path)
    queue_rows = queue_report.get("all_rows") or queue_report.get("rows") or []

    aggregated_rows: dict[tuple[str, str], dict[str, Any]] = {}
    skipped_reason_counts: Counter[str] = Counter()
    bucket_keywords: dict[str, list[str]] = {}
    bucket_sources: dict[str, list[str]] = {}

    for raw_row in queue_rows:
        if str(raw_row.get("selection_reason", "")).strip() != "selected":
            skipped_reason_counts[str(raw_row.get("selection_reason", "unselected")).strip() or "unselected"] += 1
            continue
        candidate = str(raw_row.get("candidate", "")).strip()
        bucket = str(raw_row.get("recommended_bucket", "")).strip()
        target_keyword_list = _PATCHABLE_BUCKET_KEYWORD_LISTS.get(bucket, "")
        if not candidate:
            skipped_reason_counts["missing_candidate"] += 1
            continue
        if not target_keyword_list:
            skipped_reason_counts["unsupported_bucket"] += 1
            continue
        key = (bucket, candidate)
        entry = aggregated_rows.setdefault(
            key,
            {
                "candidate": candidate,
                "recommended_bucket": bucket,
                "target_keyword_list": target_keyword_list,
                "review_ok_count": 0,
                "promotion_promoted_count": 0,
                "promotion_rejected_count": 0,
                "needs_article_body_count": 0,
                "article_enriched_review_count": 0,
                "canonical_headline_examples": [],
                "sample_event_ids": [],
            },
        )
        entry["review_ok_count"] = max(int(entry["review_ok_count"]), int(raw_row.get("review_ok_count", 0)))
        entry["promotion_promoted_count"] = max(
            int(entry["promotion_promoted_count"]),
            int(raw_row.get("promotion_promoted_count", 0)),
        )
        entry["promotion_rejected_count"] = max(
            int(entry["promotion_rejected_count"]),
            int(raw_row.get("promotion_rejected_count", 0)),
        )
        entry["needs_article_body_count"] = max(
            int(entry["needs_article_body_count"]),
            int(raw_row.get("needs_article_body_count", 0)),
        )
        entry["article_enriched_review_count"] = max(
            int(entry["article_enriched_review_count"]),
            int(raw_row.get("article_enriched_review_count", 0)),
        )
        entry["canonical_headline_examples"] = _merge_unique_strings(
            entry["canonical_headline_examples"],
            list(raw_row.get("canonical_headline_examples", []) or []),
            limit=3,
        )
        entry["sample_event_ids"] = _merge_unique_strings(
            entry["sample_event_ids"],
            list(raw_row.get("sample_event_ids", []) or []),
            limit=5,
        )
        bucket_keywords.setdefault(bucket, [])
        if candidate not in bucket_keywords[bucket]:
            bucket_keywords[bucket].append(candidate)
        bucket_sources.setdefault(bucket, [])
        if candidate not in bucket_sources[bucket]:
            bucket_sources[bucket].append(candidate)

    rows = sorted(
        aggregated_rows.values(),
        key=lambda row: (
            row["target_keyword_list"],
            -int(row["promotion_promoted_count"]),
            -int(row["review_ok_count"]),
            row["candidate"],
        ),
    )
    bucket_patches = [
        {
            "bucket": bucket,
            "target_keyword_list": _PATCHABLE_BUCKET_KEYWORD_LISTS[bucket],
            "keywords": bucket_keywords[bucket],
            "source_candidates": bucket_sources[bucket],
            "target_file": "src/kindshot/bucket.py",
        }
        for bucket in sorted(bucket_keywords.keys())
    ]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_queue_path": str(queue_path),
        "candidate_count": int(queue_report.get("candidate_count", len(queue_rows))),
        "selected_count": len(rows),
        "patch_bucket_count": len(bucket_patches),
        "skipped_reason_counts": dict(skipped_reason_counts),
        "all_rows": rows,
        "rows": rows[: max(1, limit)],
        "bucket_patches": bucket_patches,
    }
    _print_unknown_rule_patch(report)
    _write_report(_rule_patch_output_path(config, output_path), report)
    return report


def promotion_policy_name(config: Config) -> str:
    return f"paper_only_conf{max(0, config.unknown_promotion_min_confidence)}_strong_only"


def evaluate_unknown_promotion(
    config: Config,
    request: UnknownReviewRequest,
    review: UnknownReviewRecord,
) -> UnknownPromotionRecord:
    gate_reasons: list[str] = []
    if not config.unknown_paper_promotion_enabled:
        gate_reasons.append("PROMOTION_DISABLED")
    if request.runtime_mode != "paper":
        gate_reasons.append("NON_PAPER_MODE")
    if review.review_status != ReviewStatus.OK:
        gate_reasons.append(f"REVIEW_{review.review_status.value}")
    if review.suggested_bucket not in ALLOWED_PROMOTION_BUCKETS:
        gate_reasons.append("UNSUPPORTED_BUCKET")
    if not review.promote_now:
        gate_reasons.append("PROMOTE_NOW_FALSE")
    if review.needs_article_body:
        gate_reasons.append("ARTICLE_BODY_REQUIRED")
    if review.confidence < max(0, config.unknown_promotion_min_confidence):
        gate_reasons.append("CONFIDENCE_BELOW_THRESHOLD")

    return UnknownPromotionRecord(
        event_id=request.event_id,
        promoted_at=datetime.now(timezone.utc),
        runtime_mode=request.runtime_mode,
        review_status=review.review_status,
        original_bucket=Bucket.UNKNOWN,
        suggested_bucket=review.suggested_bucket,
        confidence=review.confidence,
        promotion_status=PromotionStatus.REJECTED if gate_reasons else PromotionStatus.PROMOTED,
        promotion_policy=promotion_policy_name(config),
        gate_reasons=gate_reasons,
    )


def _build_unknown_review_prompt(request: UnknownReviewRequest) -> str:
    detected_at_kst = request.detected_at.astimezone(_KST).strftime("%Y-%m-%d %H:%M:%S")
    article_text = request.article_text.strip() or "N/A"
    return f"""task: review UNKNOWN Korean stock-news headline for bucket suggestion only
headline: {request.headline}
corp_name: {request.corp_name}
ticker: {request.ticker}
detected_at_kst: {detected_at_kst}
source: {request.source}
rss_link: {request.rss_link}
article_text: {article_text}

Korean stock disclosure domain guide (공시 도메인 가이드):
POS_STRONG examples: 자사주매입/소각, 대규모 수주, 신약승인/허가, 인수합병(acquirer), 흑자전환, 실적호전, 배당증가, 무상증자, 주식분할, 자기주식처분(소각)
POS_WEAK examples: MOU/LOI 체결, 해외진출, 신사업진출, 특허취득, 정부과제선정, 임상진입
NEG_STRONG examples: 유상증자(주식희석=악재), 감사의견거절/한정, 상장폐지, 횡령/배임, 영업정지, 관리종목지정, 대규모손실, CB/BW발행(전환사채=희석)
NEG_WEAK examples: 소송제기, 벌금/과징금, 대표이사변경, 실적악화, 감자(주식병합)
IGNORE examples: 주주총회결과, 정기보고서, 임원선임, 사외이사선임, 기업설명회(IR), 단순공시정정

Key principle: 유상증자/CB/BW = dilution = NEG (not positive growth signal)

rules:
- you are reviewing only UNKNOWN headlines
- do not assume facts not present in the headline or article_text
- if headline is ambiguous, keep UNKNOWN or IGNORE
- paper-only promotion may occur later; promotion suggestion is advisory

confidence guide: 0-100 integer. 85-100=very clear signal, 70-84=likely, 50-69=uncertain, below 50=unclear/UNKNOWN
promote_now: true only if confidence>=85 and bucket is POS_STRONG or POS_WEAK

output json:
{{
  "suggested_bucket": "POS_STRONG|POS_WEAK|NEG_STRONG|NEG_WEAK|IGNORE|UNKNOWN",
  "polarity": "POSITIVE|NEGATIVE|NEUTRAL|UNCLEAR",
  "confidence": 75,
  "promote_now": false,
  "needs_article_body": false,
  "canonical_headline": "",
  "reason": "",
  "reason_codes": ["CODE"],
  "keyword_candidates": ["phrase"],
  "risk_flags": ["FLAG"]
}}"""


def _parse_unknown_review_response(raw: str) -> Optional[dict]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            return None
        try:
            payload = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None

    bucket = str(payload.get("suggested_bucket", "UNKNOWN")).strip()
    polarity = str(payload.get("polarity", "UNCLEAR")).strip()
    confidence = payload.get("confidence", 0)
    if bucket not in {member.value for member in Bucket}:
        return None
    if polarity not in {member.value for member in ReviewPolarity}:
        return None
    if not isinstance(confidence, (int, float)) or not (0 <= int(confidence) <= 100):
        return None

    def _as_str_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    return {
        "suggested_bucket": bucket,
        "polarity": polarity,
        "confidence": int(confidence),
        "promote_now": bool(payload.get("promote_now", False)),
        "needs_article_body": bool(payload.get("needs_article_body", False)),
        "canonical_headline": str(payload.get("canonical_headline", "")).strip(),
        "reason": str(payload.get("reason", "")).strip()[:200],
        "reason_codes": _as_str_list(payload.get("reason_codes")),
        "keyword_candidates": _as_str_list(payload.get("keyword_candidates")),
        "risk_flags": _as_str_list(payload.get("risk_flags")),
    }


class UnknownArticleEnricher:
    """Best-effort article/body fetcher for UNKNOWN review re-checks."""

    def __init__(self, config: Config) -> None:
        self._config = config

    async def fetch(self, request: UnknownReviewRequest) -> ArticleEnrichmentResult:
        if not request.rss_link.strip():
            return ArticleEnrichmentResult(status="empty")

        timeout = aiohttp.ClientTimeout(total=max(1.0, self._config.unknown_review_article_timeout_s))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(request.rss_link) as response:
                    if response.status >= 400:
                        return ArticleEnrichmentResult(status="fetch_error")
                    content_type = response.headers.get("Content-Type", "")
                    body = await response.text()
        except Exception:
            logger.info("UNKNOWN article enrichment fetch failed for %s", request.event_id, exc_info=True)
            return ArticleEnrichmentResult(status="fetch_error")

        limit = max(0, self._config.unknown_review_article_max_chars)
        if "html" in content_type.lower():
            article_text, body_source = _extract_article_text_from_html(body, limit=limit)
        else:
            article_text = _normalize_article_text(body, limit=limit)
            body_source = "rss_link_text"
        if not article_text:
            return ArticleEnrichmentResult(status="empty", body_source=body_source)
        return ArticleEnrichmentResult(status="fetched", article_text=article_text, body_source=body_source)


class UnknownReviewEngine:
    """LLM-based structured reviewer for UNKNOWN headlines."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._llm = LlmClient(config)
        self._article_enricher = UnknownArticleEnricher(config)

    async def review(self, request: UnknownReviewRequest) -> UnknownReviewRecord:
        headline_only = not bool(request.article_text.strip())
        review_iteration = "headline_initial" if headline_only else "article_enriched"
        body_fetch_status = "not_requested" if headline_only else "fetched"
        body_source = request.article_source if not headline_only else ""
        body_text_chars = len(request.article_text.strip()) if not headline_only else 0
        re_reviewed = not headline_only
        if not self._config.nvidia_api_key and not self._config.anthropic_api_key:
            return UnknownReviewRecord(
                event_id=request.event_id,
                reviewed_at=datetime.now(timezone.utc),
                runtime_mode=request.runtime_mode,
                headline_only=headline_only,
                review_iteration=review_iteration,
                review_status=ReviewStatus.SKIPPED,
                body_fetch_status=body_fetch_status,
                body_source=body_source,
                body_text_chars=body_text_chars,
                re_reviewed=re_reviewed,
                error="ANTHROPIC_API_KEY_MISSING",
            )

        prompt = _build_unknown_review_prompt(request)
        try:
            raw_text, _ = await self._llm.call(prompt, max_tokens=500)
            parsed = _parse_unknown_review_response(raw_text)
            if parsed is None:
                return UnknownReviewRecord(
                    event_id=request.event_id,
                    reviewed_at=datetime.now(timezone.utc),
                    runtime_mode=request.runtime_mode,
                    headline_only=headline_only,
                    review_iteration=review_iteration,
                    review_status=ReviewStatus.ERROR,
                    body_fetch_status=body_fetch_status,
                    body_source=body_source,
                    body_text_chars=body_text_chars,
                    re_reviewed=re_reviewed,
                    error="UNKNOWN_REVIEW_PARSE_ERROR",
                )
            return UnknownReviewRecord(
                event_id=request.event_id,
                reviewed_at=datetime.now(timezone.utc),
                runtime_mode=request.runtime_mode,
                headline_only=headline_only,
                review_iteration=review_iteration,
                review_status=ReviewStatus.OK,
                suggested_bucket=Bucket(parsed["suggested_bucket"]),
                polarity=ReviewPolarity(parsed["polarity"]),
                confidence=parsed["confidence"],
                promote_now=parsed["promote_now"],
                needs_article_body=parsed["needs_article_body"],
                body_fetch_status=body_fetch_status,
                body_source=body_source,
                body_text_chars=body_text_chars,
                re_reviewed=re_reviewed,
                canonical_headline=parsed["canonical_headline"],
                reason=parsed["reason"],
                reason_codes=parsed["reason_codes"],
                keyword_candidates=parsed["keyword_candidates"],
                risk_flags=parsed["risk_flags"],
            )
        except Exception as exc:
            from kindshot.llm_client import LlmTimeoutError as _LlmTimeout
            error_str = "UNKNOWN_REVIEW_TIMEOUT" if isinstance(exc, _LlmTimeout) else f"{type(exc).__name__}: {exc}"
            if not isinstance(exc, _LlmTimeout):
                logger.warning("UNKNOWN shadow review failed for %s", request.event_id, exc_info=True)
            return UnknownReviewRecord(
                event_id=request.event_id,
                reviewed_at=datetime.now(timezone.utc),
                runtime_mode=request.runtime_mode,
                headline_only=headline_only,
                review_iteration=review_iteration,
                review_status=ReviewStatus.ERROR,
                body_fetch_status=body_fetch_status,
                body_source=body_source,
                body_text_chars=body_text_chars,
                re_reviewed=re_reviewed,
                error=error_str,
            )

    async def review_with_optional_article(self, request: UnknownReviewRequest) -> list[UnknownReviewRecord]:
        initial_review = await self.review(request)
        if (
            not self._config.unknown_review_article_enrichment_enabled
            or initial_review.review_status != ReviewStatus.OK
            or not initial_review.needs_article_body
        ):
            return [initial_review]

        enrichment = await self._article_enricher.fetch(request)
        if enrichment.status != "fetched" or not enrichment.article_text.strip():
            return [
                initial_review.model_copy(
                    update={
                        "body_fetch_status": enrichment.status,
                        "body_source": enrichment.body_source,
                        "body_text_chars": len(enrichment.article_text.strip()),
                    }
                )
            ]

        enriched_request = replace(
            request,
            article_text=enrichment.article_text,
            article_source=enrichment.body_source,
        )
        enriched_review = await self.review(enriched_request)
        return [initial_review, enriched_review]
