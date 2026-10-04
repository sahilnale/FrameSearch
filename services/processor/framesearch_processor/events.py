"""Validate the frozen, keyed media.uploaded envelope before touching job state."""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

MAX_EVENT_BYTES = 16 * 1024
EVENT_FIELDS = {"event_id", "event_type", "schema_version", "video_id", "job_id", "created_at"}
UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|\+00:00)")


class EventValidationError(ValueError):
    """Malformed events have no trustworthy job to fail; do not acknowledge them."""


@dataclass(frozen=True)
class MediaUploaded:
    event_id: UUID
    video_id: UUID
    job_id: UUID
    created_at: datetime


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EventValidationError("Event contains duplicate JSON fields")
        result[key] = value
    return result


def _uuid(value, field: str) -> UUID:
    if not isinstance(value, str):
        raise EventValidationError(f"{field} must be a UUID string")
    try:
        return UUID(value)
    except ValueError:
        raise EventValidationError(f"{field} must be a UUID string") from None


def parse_media_uploaded(value: bytes | None, key: bytes | None) -> MediaUploaded:
    if not isinstance(value, bytes) or not 1 <= len(value) <= MAX_EVENT_BYTES:
        raise EventValidationError("Event must contain 1–16384 bytes of UTF-8 JSON")
    try:
        document = json.loads(value.decode("utf-8"), object_pairs_hook=_unique_fields)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise EventValidationError("Event must contain valid UTF-8 JSON") from None
    if not isinstance(document, dict) or document.keys() != EVENT_FIELDS:
        raise EventValidationError("Event must contain the six frozen envelope fields")
    if document["event_type"] != "media.uploaded":
        raise EventValidationError("Event type must be media.uploaded")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise EventValidationError("Event schema_version must be integer 1")
    event_id = _uuid(document["event_id"], "event_id")
    video_id = _uuid(document["video_id"], "video_id")
    job_id = _uuid(document["job_id"], "job_id")
    timestamp = document["created_at"]
    if not isinstance(timestamp, str) or UTC_TIMESTAMP.fullmatch(timestamp) is None:
        raise EventValidationError("Event created_at must be an RFC3339 UTC timestamp")
    try:
        created_at = datetime.fromisoformat(timestamp)
    except ValueError:
        raise EventValidationError("Event created_at must be an RFC3339 UTC timestamp") from None
    if not isinstance(key, bytes) or len(key) > 128:
        raise EventValidationError("Kafka key must match the event's video UUID")
    try:
        key_id = _uuid(key.decode("utf-8"), "Kafka key")
    except UnicodeError:
        raise EventValidationError("Kafka key must be a UTF-8 video UUID") from None
    if key_id != video_id:
        raise EventValidationError("Kafka key must match the event's video UUID")
    return MediaUploaded(event_id, video_id, job_id, created_at)
