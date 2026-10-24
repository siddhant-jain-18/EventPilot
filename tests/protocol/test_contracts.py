"""Message contract sanity: versions, correlation ids, serialization."""

from __future__ import annotations

import pytest

from shared.messages import ALL_MESSAGE_CLASSES, ErrorResponse, QuoteRequest, QuoteResponse


@pytest.mark.parametrize("cls", ALL_MESSAGE_CLASSES, ids=lambda c: c.__name__)
def test_schema_version_present(cls):
    assert cls.__fields__["schema_version"].default == "1.0.0"
    assert hasattr(cls, "TYPE") and cls.TYPE


def test_requests_have_unique_ids_and_correlation():
    req1 = QuoteRequest(event_id="E-1", attendee_count=60)
    req2 = QuoteRequest(event_id="E-1", attendee_count=60)
    assert req1.request_id != req2.request_id
    assert req1.request_id.startswith("REQ-")
    req1.correlation_id = "COR-42"
    assert req1.correlation_id == "COR-42"


def test_json_roundtrip_all_messages():
    for cls in ALL_MESSAGE_CLASSES:
        kwargs = {}
        for name, spec in cls.__fields__.items():
            if spec.required:
                if spec.type_ is int or "int" in str(spec.type_).lower():
                    kwargs[name] = 0
                else:
                    kwargs[name] = "X"
        if "attendee_count" in kwargs:
            kwargs["attendee_count"] = 60
        instance = cls(**kwargs)
        raw = instance.json()
        clone = cls.parse_raw(raw)
        assert clone.dict() == instance.dict(), cls.__name__


def test_failure_response_carries_typed_error():
    err = ValueError("boom")
    response = QuoteResponse.failure("REQ-9", err, event_id="E-1")
    assert response.status == "failed"
    assert response.error.code == "internal_error"
    assert response.request_id == "REQ-9"


def test_error_response_shape():
    e = ErrorResponse(code="expired", message="too late", retryable=False)
    assert e.code == "expired" and e.retryable is False


def test_response_requires_request_id_echo():
    with pytest.raises(Exception):
        QuoteResponse()  # request_id is mandatory: responses must be correlated
