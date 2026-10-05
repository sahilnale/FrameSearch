import json
from datetime import UTC
from uuid import uuid4

import pytest

from framesearch_processor.events import EventValidationError, parse_media_uploaded


@pytest.fixture
def envelope():
    return {
        "event_id": str(uuid4()),
        "event_type": "media.uploaded",
        "schema_version": 1,
        "video_id": str(uuid4()),
        "job_id": str(uuid4()),
        "created_at": "2026-10-07T12:34:56.123456789Z",
    }


def parse(document, key=None):
    return parse_media_uploaded(
        json.dumps(document).encode(), (document["video_id"].encode() if key is None else key)
    )


@pytest.mark.parametrize(
    "timestamp",
    ["2026-10-07T12:34:56Z", "2026-10-07T12:34:56.123456789Z", "2026-10-07T12:34:56+00:00"],
)
def test_go_compatible_event_returns_typed_ids_and_utc_timestamp(envelope, timestamp):
    envelope["created_at"] = timestamp
    result = parse(envelope)
    assert str(result.event_id) == envelope["event_id"]
    assert str(result.video_id) == envelope["video_id"]
    assert str(result.job_id) == envelope["job_id"]
    assert result.created_at.tzinfo == UTC


@pytest.mark.parametrize("field", ["event_id", "video_id", "job_id"])
@pytest.mark.parametrize("value", [None, 123, "not-a-uuid"])
def test_invalid_identity_is_rejected(envelope, field, value):
    envelope[field] = value
    with pytest.raises(EventValidationError, match=field):
        parse_media_uploaded(json.dumps(envelope).encode(), str(uuid4()).encode())


@pytest.mark.parametrize("version", [True, "1", 1.0, 0, 2])
def test_unknown_or_wrong_type_schema_version_is_rejected(envelope, version):
    envelope["schema_version"] = version
    with pytest.raises(EventValidationError, match="schema_version"):
        parse(envelope)


@pytest.mark.parametrize(
    "timestamp",
    [
        None,
        "2026-10-07",
        "2026-10-07T12:34:56",
        "2026-10-07T12:34:56-07:00",
        "2026-02-30T12:34:56Z",
        "not-a-date",
    ],
)
def test_missing_or_invalid_utc_timestamp_is_rejected(envelope, timestamp):
    envelope["created_at"] = timestamp
    with pytest.raises(EventValidationError, match="created_at"):
        parse(envelope)


@pytest.mark.parametrize("payload", [None, b"", b"x" * 16385, b"\xff", b"{", b"[]", b"null", b"42"])
def test_missing_malformed_or_nonobject_payload_is_rejected(payload):
    with pytest.raises(EventValidationError):
        parse_media_uploaded(payload, str(uuid4()).encode())


@pytest.mark.parametrize("key", [None, b"", b"\xff", b"not-a-uuid", b"x" * 129])
def test_missing_or_invalid_partition_key_is_rejected(envelope, key):
    with pytest.raises(EventValidationError, match="key"):
        parse_media_uploaded(json.dumps(envelope).encode(), key)


def test_mismatched_video_partition_key_is_rejected(envelope):
    with pytest.raises(EventValidationError, match="key"):
        parse(envelope, str(uuid4()).encode())


@pytest.mark.parametrize("change", ["missing", "extra", "type", "duplicate"])
def test_changed_envelope_or_duplicate_json_field_is_rejected(envelope, change):
    if change == "missing":
        del envelope["event_id"]
    elif change == "extra":
        envelope["unexpected"] = "value"
    elif change == "type":
        envelope["event_type"] = "media.processed"
    payload = json.dumps(envelope)
    if change == "duplicate":
        payload = payload[:-1] + ', "schema_version": 1}'
    with pytest.raises(EventValidationError):
        parse_media_uploaded(payload.encode(), envelope["video_id"].encode())
