from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from kindshot.config import Config
from kindshot.models import Bucket, PromotionStatus, ReviewPolarity, ReviewStatus, UnknownReviewRecord
from kindshot.unknown_review import (
    ArticleEnrichmentResult,
    UnknownReviewEngine,
    UnknownReviewRequest,
    _parse_unknown_review_response,
    append_unknown_inbox,
    append_unknown_promotion,
    append_unknown_review,
    evaluate_unknown_promotion,
    unknown_review_ops_summary,
    unknown_review_rule_patch,
    unknown_review_rule_report,
    unknown_review_rule_queue,
)


def _request() -> UnknownReviewRequest:
    return UnknownReviewRequest(
        event_id="evt_123",
        detected_at=datetime(2026, 3, 16, 1, 0, tzinfo=timezone.utc),
        runtime_mode="paper",
        ticker="005930",
        corp_name="삼성전자",
        headline="삼성전자, 신규 AI 반도체 협력 확대",
        rss_link="https://example.com/news",
        rss_guid="guid-1",
        published="2026-03-16T10:00:00+09:00",
        source="KIND",
    )


def test_parse_unknown_review_response_accepts_valid_json():
    payload = _parse_unknown_review_response(
        """{
          "suggested_bucket": "POS_STRONG",
          "polarity": "POSITIVE",
          "confidence": 87,
          "promote_now": true,
          "needs_article_body": false,
          "canonical_headline": "AI 반도체 협력 확대",
          "reason": "신규 협력 확대 시그널",
          "reason_codes": ["NEW_PARTNERSHIP"],
          "keyword_candidates": ["협력 확대"],
          "risk_flags": []
        }"""
    )

    assert payload is not None
    assert payload["suggested_bucket"] == "POS_STRONG"
    assert payload["confidence"] == 87
    assert payload["promote_now"] is True


def test_append_unknown_inbox_and_review_write_daily_jsonl(tmp_path):
    cfg = Config(
        unknown_inbox_dir=tmp_path / "logs" / "unknown_inbox",
        unknown_review_dir=tmp_path / "logs" / "unknown_review",
        unknown_promotion_dir=tmp_path / "logs" / "unknown_promotion",
    )
    request = _request()
    append_unknown_inbox(cfg, request)
    append_unknown_review(
        cfg,
        request.detected_at,
        record=SimpleNamespace(
            model_dump=lambda mode="json": {
                "type": "unknown_review",
                "event_id": request.event_id,
                "review_status": "SKIPPED",
            }
        ),
    )
    append_unknown_promotion(
        cfg,
        request.detected_at,
        record=SimpleNamespace(
            model_dump=lambda mode="json": {
                "type": "unknown_promotion",
                "event_id": request.event_id,
                "promotion_status": "REJECTED",
            }
        ),
    )

    inbox_path = cfg.unknown_inbox_dir / "2026-03-16.jsonl"
    review_path = cfg.unknown_review_dir / "2026-03-16.jsonl"
    promotion_path = cfg.unknown_promotion_dir / "2026-03-16.jsonl"
    assert inbox_path.exists()
    assert review_path.exists()
    assert promotion_path.exists()
    inbox_row = json.loads(inbox_path.read_text(encoding="utf-8").splitlines()[0])
    review_row = json.loads(review_path.read_text(encoding="utf-8").splitlines()[0])
    promotion_row = json.loads(promotion_path.read_text(encoding="utf-8").splitlines()[0])
    assert inbox_row["event_id"] == "evt_123"
    assert inbox_row["original_bucket"] == "UNKNOWN"
    assert review_row["review_status"] == "SKIPPED"
    assert promotion_row["promotion_status"] == "REJECTED"


async def test_unknown_review_engine_skips_without_api_key(tmp_path):
    cfg = Config(anthropic_api_key="", nvidia_api_key="", unknown_review_dir=tmp_path / "logs" / "unknown_review")
    engine = UnknownReviewEngine(cfg)

    record = await engine.review(_request())

    assert record.review_status == ReviewStatus.SKIPPED
    assert record.error == "ANTHROPIC_API_KEY_MISSING"


async def test_unknown_review_engine_parses_structured_response(tmp_path):
    cfg = Config(anthropic_api_key="test")
    engine = UnknownReviewEngine(cfg)
    mock_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="text",
                            text=json.dumps(
                                {
                                    "suggested_bucket": "POS_STRONG",
                                    "polarity": "POSITIVE",
                                    "confidence": 91,
                                    "promote_now": True,
                                    "needs_article_body": False,
                                    "canonical_headline": "AI 반도체 협력 확대",
                                    "reason": "협력 확대 공시로 해석 가능",
                                    "reason_codes": ["NEW_PARTNERSHIP"],
                                    "keyword_candidates": ["협력 확대"],
                                    "risk_flags": [],
                                }
                            ),
                        )
                    ]
                )
            )
        )
    )

    with patch.object(engine._llm, "_get_anthropic_client", return_value=mock_client):
        record = await engine.review(_request())

    assert record.review_status == ReviewStatus.OK
    assert record.suggested_bucket == Bucket.POS_STRONG
    assert record.polarity == ReviewPolarity.POSITIVE
    assert record.confidence == 91


async def test_unknown_review_engine_rereviews_with_article_enrichment(tmp_path):
    cfg = Config(
        anthropic_api_key="test",
        unknown_review_article_enrichment_enabled=True,
    )
    engine = UnknownReviewEngine(cfg)
    mock_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                side_effect=[
                    SimpleNamespace(
                        content=[
                            SimpleNamespace(
                                type="text",
                                text=json.dumps(
                                    {
                                        "suggested_bucket": "UNKNOWN",
                                        "polarity": "UNCLEAR",
                                        "confidence": 55,
                                        "promote_now": False,
                                        "needs_article_body": True,
                                        "canonical_headline": "AI 협력 확대",
                                        "reason": "헤드라인만으로는 계약 성격 불명확",
                                        "reason_codes": ["AMBIGUOUS_HEADLINE"],
                                        "keyword_candidates": ["협력 확대"],
                                        "risk_flags": ["NEEDS_BODY"],
                                    }
                                ),
                            )
                        ]
                    ),
                    SimpleNamespace(
                        content=[
                            SimpleNamespace(
                                type="text",
                                text=json.dumps(
                                    {
                                        "suggested_bucket": "POS_STRONG",
                                        "polarity": "POSITIVE",
                                        "confidence": 90,
                                        "promote_now": True,
                                        "needs_article_body": False,
                                        "canonical_headline": "AI 반도체 공급 계약 체결",
                                        "reason": "본문에서 계약 체결과 규모 확인",
                                        "reason_codes": ["SUPPLY_CONTRACT"],
                                        "keyword_candidates": ["공급 계약 체결"],
                                        "risk_flags": [],
                                    }
                                ),
                            )
                        ]
                    ),
                ]
            )
        )
    )

    with patch.object(engine._llm, "_get_anthropic_client", return_value=mock_client), patch.object(
        engine._article_enricher,
        "fetch",
        AsyncMock(
            return_value=ArticleEnrichmentResult(
                status="fetched",
                article_text="삼성전자가 대형 AI 반도체 공급 계약을 체결했다.",
                body_source="rss_link_html:article",
            )
        ),
    ):
        reviews = await engine.review_with_optional_article(_request())

    assert len(reviews) == 2
    assert reviews[0].review_iteration == "headline_initial"
    assert reviews[0].headline_only is True
    assert reviews[1].review_iteration == "article_enriched"
    assert reviews[1].headline_only is False
    assert reviews[1].body_fetch_status == "fetched"
    assert reviews[1].body_source == "rss_link_html:article"
    assert reviews[1].re_reviewed is True
    assert reviews[1].suggested_bucket == Bucket.POS_STRONG
    assert reviews[1].needs_article_body is False


async def test_unknown_review_engine_records_fetch_failure_without_blocking(tmp_path):
    cfg = Config(
        anthropic_api_key="test",
        unknown_review_article_enrichment_enabled=True,
    )
    engine = UnknownReviewEngine(cfg)
    mock_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="text",
                            text=json.dumps(
                                {
                                    "suggested_bucket": "UNKNOWN",
                                    "polarity": "UNCLEAR",
                                    "confidence": 52,
                                    "promote_now": False,
                                    "needs_article_body": True,
                                    "canonical_headline": "AI 협력 확대",
                                    "reason": "헤드라인만으로는 불충분",
                                    "reason_codes": ["AMBIGUOUS_HEADLINE"],
                                    "keyword_candidates": [],
                                    "risk_flags": ["NEEDS_BODY"],
                                }
                            ),
                        )
                    ]
                )
            )
        )
    )

    with patch.object(engine._llm, "_get_anthropic_client", return_value=mock_client), patch.object(
        engine._article_enricher,
        "fetch",
        AsyncMock(return_value=ArticleEnrichmentResult(status="fetch_error")),
    ):
        reviews = await engine.review_with_optional_article(_request())

    assert len(reviews) == 1
    assert reviews[0].review_iteration == "headline_initial"
    assert reviews[0].headline_only is True
    assert reviews[0].body_fetch_status == "fetch_error"
    assert reviews[0].needs_article_body is True


def test_evaluate_unknown_promotion_rejects_below_threshold(tmp_path):
    cfg = Config(
        unknown_paper_promotion_enabled=True,
        unknown_promotion_min_confidence=85,
    )
    review = UnknownReviewRecord(
        event_id="evt_123",
        reviewed_at=datetime.now(timezone.utc),
        runtime_mode="paper",
        headline_only=True,
        review_status=ReviewStatus.OK,
        suggested_bucket=Bucket.POS_STRONG,
        confidence=84,
        promote_now=True,
    )

    record = evaluate_unknown_promotion(cfg, _request(), review)

    assert record.promotion_status == PromotionStatus.REJECTED
    assert "CONFIDENCE_BELOW_THRESHOLD" in record.gate_reasons


def test_evaluate_unknown_promotion_accepts_ready_pos_strong(tmp_path):
    cfg = Config(
        unknown_paper_promotion_enabled=True,
        unknown_promotion_min_confidence=85,
    )
    review = UnknownReviewRecord(
        event_id="evt_123",
        reviewed_at=datetime.now(timezone.utc),
        runtime_mode="paper",
        headline_only=True,
        review_status=ReviewStatus.OK,
        suggested_bucket=Bucket.POS_STRONG,
        confidence=91,
        promote_now=True,
        needs_article_body=False,
    )

    record = evaluate_unknown_promotion(cfg, _request(), review)

    assert record.promotion_status == PromotionStatus.PROMOTED
    assert record.gate_reasons == []


def test_evaluate_unknown_promotion_rejects_pos_weak_when_news_weak_disabled(tmp_path):
    cfg = Config(
        unknown_paper_promotion_enabled=True,
        news_weak_enabled=False,
    )
    review = UnknownReviewRecord(
        event_id="evt_123",
        reviewed_at=datetime.now(timezone.utc),
        runtime_mode="paper",
        headline_only=True,
        review_status=ReviewStatus.OK,
        suggested_bucket=Bucket.POS_WEAK,
        confidence=91,
        promote_now=True,
        needs_article_body=False,
    )

    record = evaluate_unknown_promotion(cfg, _request(), review)

    assert record.promotion_status == PromotionStatus.REJECTED
    assert "NEWS_WEAK_DISABLED" in record.gate_reasons


def test_unknown_review_ops_summary_aggregates_daily_health(tmp_path):
    cfg = Config(
        unknown_inbox_dir=tmp_path / "logs" / "unknown_inbox",
        unknown_review_dir=tmp_path / "logs" / "unknown_review",
        unknown_promotion_dir=tmp_path / "logs" / "unknown_promotion",
        unknown_review_ops_summary_path=tmp_path / "data" / "unknown_review" / "ops" / "latest.json",
    )
    request = _request()
    append_unknown_inbox(cfg, request)
    append_unknown_review(
        cfg,
        request.detected_at,
        UnknownReviewRecord(
            event_id=request.event_id,
            reviewed_at=datetime.now(timezone.utc),
            runtime_mode="paper",
            headline_only=True,
            review_status=ReviewStatus.OK,
            suggested_bucket=Bucket.POS_STRONG,
            confidence=91,
            promote_now=True,
            needs_article_body=False,
        ),
    )
    append_unknown_promotion(
        cfg,
        request.detected_at,
        SimpleNamespace(
            model_dump=lambda mode="json": {
                "type": "unknown_promotion",
                "event_id": request.event_id,
                "promotion_status": "PROMOTED",
                "gate_reasons": [],
            }
        ),
    )
    # second day with backlog only
    backlog_request = UnknownReviewRequest(
        event_id="evt_backlog",
        detected_at=datetime(2026, 3, 15, 1, 0, tzinfo=timezone.utc),
        runtime_mode="paper",
        ticker="000660",
        corp_name="하이닉스",
        headline="하이닉스, 신규 투자 검토",
        rss_link="https://example.com/backlog",
        rss_guid="guid-backlog",
        published="2026-03-15T10:00:00+09:00",
        source="KIND",
    )
    append_unknown_inbox(cfg, backlog_request)

    report = unknown_review_ops_summary(cfg, limit=5)

    assert report["date_count"] == 2
    assert report["health_counts"]["healthy"] == 1
    assert report["health_counts"]["review_backlog"] == 1
    first_row = report["rows"][0]
    assert first_row["promotion_promoted_count"] == 1
    assert first_row["review_ok_count"] == 1


def test_unknown_review_ops_summary_tracks_article_enrichment(tmp_path):
    cfg = Config(
        unknown_inbox_dir=tmp_path / "logs" / "unknown_inbox",
        unknown_review_dir=tmp_path / "logs" / "unknown_review",
        unknown_promotion_dir=tmp_path / "logs" / "unknown_promotion",
    )
    request = _request()
    append_unknown_inbox(cfg, request)
    append_unknown_review(
        cfg,
        request.detected_at,
        UnknownReviewRecord(
            event_id=request.event_id,
            reviewed_at=datetime.now(timezone.utc),
            runtime_mode="paper",
            headline_only=True,
            review_iteration="headline_initial",
            review_status=ReviewStatus.OK,
            suggested_bucket=Bucket.UNKNOWN,
            confidence=55,
            promote_now=False,
            needs_article_body=True,
        ),
    )
    append_unknown_review(
        cfg,
        request.detected_at,
        UnknownReviewRecord(
            event_id=request.event_id,
            reviewed_at=datetime.now(timezone.utc),
            runtime_mode="paper",
            headline_only=False,
            review_iteration="article_enriched",
            review_status=ReviewStatus.OK,
            suggested_bucket=Bucket.POS_STRONG,
            confidence=90,
            promote_now=True,
            needs_article_body=False,
            body_fetch_status="fetched",
            body_source="rss_link_html:article",
            body_text_chars=120,
            re_reviewed=True,
            canonical_headline="AI 반도체 공급 계약 체결",
            keyword_candidates=["공급 계약 체결"],
        ),
    )

    report = unknown_review_ops_summary(cfg, limit=1)

    row = report["rows"][0]
    assert row["article_enriched_review_count"] == 1
    assert row["article_fetch_success_count"] == 1


def test_unknown_review_ops_summary_writes_explicit_output(tmp_path):
    cfg = Config(
        unknown_inbox_dir=tmp_path / "logs" / "unknown_inbox",
        unknown_review_dir=tmp_path / "logs" / "unknown_review",
        unknown_promotion_dir=tmp_path / "logs" / "unknown_promotion",
    )
    append_unknown_inbox(cfg, _request())
    output_path = tmp_path / "report.json"

    report = unknown_review_ops_summary(cfg, limit=1, output_path=str(output_path))

    assert report["date_count"] == 1
    assert output_path.exists()
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["date_count"] == 1


def test_unknown_review_rule_report_aggregates_candidates_and_promotions(tmp_path):
    cfg = Config(
        unknown_review_dir=tmp_path / "logs" / "unknown_review",
        unknown_promotion_dir=tmp_path / "logs" / "unknown_promotion",
        unknown_review_rule_report_path=tmp_path / "data" / "unknown_review" / "rule_report" / "latest.json",
    )
    detected_at = datetime(2026, 3, 16, 1, 0, tzinfo=timezone.utc)
    append_unknown_review(
        cfg,
        detected_at,
        UnknownReviewRecord(
            event_id="evt_1",
            reviewed_at=datetime.now(timezone.utc),
            runtime_mode="paper",
            headline_only=True,
            review_status=ReviewStatus.OK,
            suggested_bucket=Bucket.POS_STRONG,
            confidence=91,
            promote_now=True,
            canonical_headline="AI 반도체 협력 확대",
            reason_codes=["NEW_PARTNERSHIP"],
            keyword_candidates=["협력 확대"],
        ),
    )
    append_unknown_review(
        cfg,
        detected_at,
        UnknownReviewRecord(
            event_id="evt_2",
            reviewed_at=datetime.now(timezone.utc),
            runtime_mode="paper",
            headline_only=True,
            review_status=ReviewStatus.OK,
            suggested_bucket=Bucket.POS_STRONG,
            confidence=88,
            promote_now=True,
            canonical_headline="AI 반도체 협력 확대",
            reason_codes=["NEW_PARTNERSHIP"],
            keyword_candidates=["협력 확대"],
            needs_article_body=True,
        ),
    )
    append_unknown_promotion(
        cfg,
        detected_at,
        SimpleNamespace(
            model_dump=lambda mode="json": {
                "type": "unknown_promotion",
                "event_id": "evt_1",
                "promotion_status": "PROMOTED",
                "gate_reasons": [],
            }
        ),
    )
    append_unknown_promotion(
        cfg,
        detected_at,
        SimpleNamespace(
            model_dump=lambda mode="json": {
                "type": "unknown_promotion",
                "event_id": "evt_2",
                "promotion_status": "REJECTED",
                "gate_reasons": ["ARTICLE_BODY_REQUIRED"],
            }
        ),
    )

    report = unknown_review_rule_report(cfg, limit=5)

    assert report["date_count"] == 1
    assert report["candidate_count"] == 1
    row = report["rows"][0]
    assert row["candidate_count"] == 1
    candidate = row["top_candidates"][0]
    assert candidate["candidate"] == "협력 확대"
    assert candidate["review_ok_count"] == 2
    assert candidate["promotion_promoted_count"] == 1
    assert candidate["promotion_rejected_count"] == 1
    assert candidate["needs_article_body_count"] == 1


def test_unknown_review_rule_report_counts_article_enriched_reviews(tmp_path):
    cfg = Config(
        unknown_review_dir=tmp_path / "logs" / "unknown_review",
        unknown_promotion_dir=tmp_path / "logs" / "unknown_promotion",
    )
    detected_at = datetime(2026, 3, 16, 1, 0, tzinfo=timezone.utc)
    append_unknown_review(
        cfg,
        detected_at,
        UnknownReviewRecord(
            event_id="evt_enriched",
            reviewed_at=datetime.now(timezone.utc),
            runtime_mode="paper",
            headline_only=False,
            review_iteration="article_enriched",
            review_status=ReviewStatus.OK,
            suggested_bucket=Bucket.POS_STRONG,
            confidence=92,
            promote_now=True,
            needs_article_body=False,
            body_fetch_status="fetched",
            body_source="rss_link_html:article",
            body_text_chars=140,
            re_reviewed=True,
            canonical_headline="AI 반도체 공급 계약 체결",
            keyword_candidates=["공급 계약 체결"],
        ),
    )

    report = unknown_review_rule_report(cfg, limit=1)

    candidate = report["rows"][0]["top_candidates"][0]
    assert candidate["article_enriched_review_count"] == 1


def test_unknown_review_rule_report_uses_canonical_headline_fallback_and_output_override(tmp_path):
    cfg = Config(
        unknown_review_dir=tmp_path / "logs" / "unknown_review",
        unknown_promotion_dir=tmp_path / "logs" / "unknown_promotion",
    )
    detected_at = datetime(2026, 3, 15, 1, 0, tzinfo=timezone.utc)
    append_unknown_review(
        cfg,
        detected_at,
        UnknownReviewRecord(
            event_id="evt_canonical",
            reviewed_at=datetime.now(timezone.utc),
            runtime_mode="paper",
            headline_only=True,
            review_status=ReviewStatus.OK,
            suggested_bucket=Bucket.NEG_STRONG,
            confidence=90,
            promote_now=True,
            canonical_headline="공급 계약 해지",
            reason_codes=["CONTRACT_CANCEL"],
            keyword_candidates=[],
        ),
    )
    output_path = tmp_path / "rule_report.json"

    report = unknown_review_rule_report(cfg, limit=1, output_path=str(output_path))

    assert report["date_count"] == 1
    candidate = report["rows"][0]["top_candidates"][0]
    assert candidate["candidate"] == "공급 계약 해지"
    assert output_path.exists()
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["candidate_count"] == 1


def test_unknown_review_rule_queue_selects_candidates_and_filters_existing_keywords(tmp_path):
    cfg = Config(
        unknown_review_dir=tmp_path / "logs" / "unknown_review",
        unknown_promotion_dir=tmp_path / "logs" / "unknown_promotion",
        unknown_review_rule_queue_path=tmp_path / "data" / "unknown_review" / "rule_queue" / "latest.json",
        unknown_rule_queue_min_reviews=2,
        unknown_rule_queue_min_promoted=1,
    )
    detected_at = datetime(2026, 3, 16, 1, 0, tzinfo=timezone.utc)
    for event_id in ("evt_a1", "evt_a2"):
        append_unknown_review(
            cfg,
            detected_at,
            UnknownReviewRecord(
                event_id=event_id,
                reviewed_at=datetime.now(timezone.utc),
                runtime_mode="paper",
                headline_only=True,
                review_status=ReviewStatus.OK,
                suggested_bucket=Bucket.POS_STRONG,
                confidence=91,
                promote_now=True,
                canonical_headline="AI 반도체 협력 확대",
                keyword_candidates=["협력 확대"],
            ),
        )
    append_unknown_promotion(
        cfg,
        detected_at,
        SimpleNamespace(
            model_dump=lambda mode="json": {
                "type": "unknown_promotion",
                "event_id": "evt_a1",
                "promotion_status": "PROMOTED",
                "gate_reasons": [],
            }
        ),
    )
    append_unknown_review(
        cfg,
        detected_at,
        UnknownReviewRecord(
            event_id="evt_existing",
            reviewed_at=datetime.now(timezone.utc),
            runtime_mode="paper",
            headline_only=True,
            review_status=ReviewStatus.OK,
            suggested_bucket=Bucket.POS_STRONG,
            confidence=90,
            promote_now=True,
            canonical_headline="기존 키워드",
            keyword_candidates=["공급계약"],
        ),
    )
    append_unknown_promotion(
        cfg,
        detected_at,
        SimpleNamespace(
            model_dump=lambda mode="json": {
                "type": "unknown_promotion",
                "event_id": "evt_existing",
                "promotion_status": "PROMOTED",
                "gate_reasons": [],
            }
        ),
    )

    report = unknown_review_rule_queue(cfg, limit=5)

    assert report["candidate_count"] == 2
    assert report["selected_count"] == 1
    assert report["already_exists_count"] == 1
    assert report["selection_reason_counts"]["selected"] == 1
    assert report["selection_reason_counts"]["already_exists"] == 1
    assert report["rows"][0]["candidate"] == "협력 확대"
    assert report["rows"][0]["recommended_bucket"] == "POS_STRONG"


def test_unknown_review_rule_queue_writes_explicit_output(tmp_path):
    cfg = Config(
        unknown_review_dir=tmp_path / "logs" / "unknown_review",
        unknown_promotion_dir=tmp_path / "logs" / "unknown_promotion",
    )
    detected_at = datetime(2026, 3, 15, 1, 0, tzinfo=timezone.utc)
    append_unknown_review(
        cfg,
        detected_at,
        UnknownReviewRecord(
            event_id="evt_queue",
            reviewed_at=datetime.now(timezone.utc),
            runtime_mode="paper",
            headline_only=True,
            review_status=ReviewStatus.OK,
            suggested_bucket=Bucket.NEG_STRONG,
            confidence=90,
            promote_now=True,
            canonical_headline="신규 리스크 경고",
            keyword_candidates=["리스크 경고"],
        ),
    )
    append_unknown_promotion(
        cfg,
        detected_at,
        SimpleNamespace(
            model_dump=lambda mode="json": {
                "type": "unknown_promotion",
                "event_id": "evt_queue",
                "promotion_status": "PROMOTED",
                "gate_reasons": [],
            }
        ),
    )
    output_path = tmp_path / "rule_queue.json"

    report = unknown_review_rule_queue(cfg, limit=1, output_path=str(output_path))

    assert report["candidate_count"] == 1
    assert report["selected_count"] == 0
    assert output_path.exists()
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["selected_count"] == 0


def test_unknown_review_rule_patch_builds_bucket_drafts_from_selected_queue(tmp_path):
    queue_path = tmp_path / "data" / "unknown_review" / "rule_queue" / "latest.json"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(
        json.dumps(
            {
                "candidate_count": 3,
                "all_rows": [
                    {
                        "candidate": "공급 계약 체결",
                        "recommended_bucket": "POS_STRONG",
                        "review_ok_count": 3,
                        "promotion_promoted_count": 2,
                        "promotion_rejected_count": 0,
                        "needs_article_body_count": 0,
                        "article_enriched_review_count": 1,
                        "selection_reason": "selected",
                        "canonical_headline_examples": ["AI 반도체 공급 계약 체결"],
                        "sample_event_ids": ["evt_1", "evt_2"],
                    },
                    {
                        "candidate": "규제 완화 기대",
                        "recommended_bucket": "POS_WEAK",
                        "review_ok_count": 2,
                        "promotion_promoted_count": 1,
                        "promotion_rejected_count": 0,
                        "needs_article_body_count": 0,
                        "article_enriched_review_count": 0,
                        "selection_reason": "selected",
                        "canonical_headline_examples": ["규제 완화 기대감 부각"],
                        "sample_event_ids": ["evt_3"],
                    },
                    {
                        "candidate": "기존 키워드",
                        "recommended_bucket": "POS_STRONG",
                        "review_ok_count": 2,
                        "promotion_promoted_count": 1,
                        "promotion_rejected_count": 0,
                        "needs_article_body_count": 0,
                        "article_enriched_review_count": 0,
                        "selection_reason": "already_exists",
                        "canonical_headline_examples": [],
                        "sample_event_ids": ["evt_existing"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = Config(
        unknown_review_rule_queue_path=queue_path,
        unknown_review_rule_patch_path=tmp_path / "data" / "unknown_review" / "rule_patch" / "latest.json",
    )

    report = unknown_review_rule_patch(cfg, limit=5)

    assert report["selected_count"] == 2
    assert report["patch_bucket_count"] == 2
    assert report["bucket_patches"][0]["target_file"] == "src/kindshot/bucket.py"
    assert report["bucket_patches"][0]["target_keyword_list"] == "POS_STRONG_KEYWORDS"
    assert "공급 계약 체결" in report["bucket_patches"][0]["keywords"]
    assert report["rows"][0]["article_enriched_review_count"] == 1
    assert report["skipped_reason_counts"]["already_exists"] == 1


def test_unknown_review_rule_patch_dedupes_same_bucket_candidates_and_writes_output(tmp_path):
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(
        json.dumps(
            {
                "candidate_count": 2,
                "all_rows": [
                    {
                        "candidate": "공급 계약 체결",
                        "recommended_bucket": "POS_STRONG",
                        "review_ok_count": 2,
                        "promotion_promoted_count": 1,
                        "promotion_rejected_count": 0,
                        "needs_article_body_count": 0,
                        "article_enriched_review_count": 0,
                        "selection_reason": "selected",
                        "canonical_headline_examples": ["예시 1"],
                        "sample_event_ids": ["evt_1"],
                    },
                    {
                        "candidate": "공급 계약 체결",
                        "recommended_bucket": "POS_STRONG",
                        "review_ok_count": 4,
                        "promotion_promoted_count": 3,
                        "promotion_rejected_count": 0,
                        "needs_article_body_count": 0,
                        "article_enriched_review_count": 2,
                        "selection_reason": "selected",
                        "canonical_headline_examples": ["예시 2"],
                        "sample_event_ids": ["evt_2"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = Config(unknown_review_rule_queue_path=queue_path)
    output_path = tmp_path / "rule_patch.json"

    report = unknown_review_rule_patch(cfg, limit=1, output_path=str(output_path))

    assert report["selected_count"] == 1
    assert report["rows"][0]["promotion_promoted_count"] == 3
    assert report["rows"][0]["article_enriched_review_count"] == 2
    assert report["bucket_patches"][0]["keywords"] == ["공급 계약 체결"]
    assert output_path.exists()


def test_unknown_review_rule_patch_skips_unsupported_bucket_rows(tmp_path):
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(
        json.dumps(
            {
                "candidate_count": 1,
                "all_rows": [
                    {
                        "candidate": "애매한 표현",
                        "recommended_bucket": "UNKNOWN",
                        "review_ok_count": 2,
                        "promotion_promoted_count": 0,
                        "promotion_rejected_count": 0,
                        "needs_article_body_count": 0,
                        "article_enriched_review_count": 0,
                        "selection_reason": "selected",
                        "canonical_headline_examples": [],
                        "sample_event_ids": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = Config(unknown_review_rule_queue_path=queue_path)

    report = unknown_review_rule_patch(cfg, limit=5)

    assert report["selected_count"] == 0
    assert report["patch_bucket_count"] == 0
    assert report["skipped_reason_counts"]["unsupported_bucket"] == 1
