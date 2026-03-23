"""Tests for domain-specific error hierarchy."""

from kindshot.errors import (
    KindshotError,
    KisApiError,
    KisAuthError,
    KisRateLimitError,
    CollectorError,
    CollectorNewsError,
    CollectorPriceError,
    ReplayError,
    ReplayDataError,
    PipelineError,
    EventProcessingError,
)


def test_hierarchy():
    assert issubclass(KisApiError, KindshotError)
    assert issubclass(KisAuthError, KisApiError)
    assert issubclass(KisRateLimitError, KisApiError)
    assert issubclass(CollectorError, KindshotError)
    assert issubclass(CollectorNewsError, CollectorError)
    assert issubclass(ReplayError, KindshotError)
    assert issubclass(ReplayDataError, ReplayError)
    assert issubclass(PipelineError, KindshotError)
    assert issubclass(EventProcessingError, PipelineError)


def test_kis_api_error_fields():
    err = KisApiError("fail", endpoint="/v1/price", status_code=500)
    assert err.endpoint == "/v1/price"
    assert err.status_code == 500
    assert "fail" in str(err)


def test_collector_error_date():
    err = CollectorNewsError("no data", date="20260316")
    assert err.date == "20260316"
    assert isinstance(err, CollectorError)
    assert isinstance(err, KindshotError)


def test_event_processing_error():
    err = EventProcessingError("parse failed", event_id="evt_001")
    assert err.event_id == "evt_001"
    assert isinstance(err, PipelineError)


def test_all_catchable_by_base():
    errors = [
        KisApiError("a"),
        KisAuthError("b"),
        CollectorError("c"),
        ReplayError("d"),
        PipelineError("e"),
    ]
    for err in errors:
        try:
            raise err
        except KindshotError:
            pass  # All should be caught
