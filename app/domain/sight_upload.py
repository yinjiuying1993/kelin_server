"""Sight upload path, signing, JPEG magic/hash, and cleanup due rules. Spec §§8.4, 11.3, 11.4."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import UUID

SIGHT_UPLOAD_OPERATION = "storage.sight_upload_url"
SIGHT_BUCKET = "kelin-sight"
SIGHT_PATH_PREFIX = "sight-temp"
SIGHT_OBJECT_EXT = ".jpg"
ALLOWED_MIME = "image/jpeg"
PUT_METHOD: Final[Literal["PUT"]] = "PUT"
MIN_SIZE_BYTES = 1
MAX_SIZE_BYTES = 5 * 1024 * 1024
UPLOAD_TTL = timedelta(minutes=10)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ACTIVE_UPLOAD_STATUSES = frozenset({"issued", "uploaded", "verifying"})
_SIGNED_PUT_ORIGIN = "https://kelin.invalid/object"
JPEG_MAGIC = b"\xff\xd8\xff"
ORIGINAL_RETENTION = timedelta(hours=24)
UPLOAD_CLEANUP_EVENT = "upload.cleanup"
UPLOAD_CLEANUP_AGGREGATE = "sight_upload"


class InvalidSightUpload(ValueError):
    """MIME, size, hash, or object path failed the locked contract."""


def sight_object_path(user_id: UUID, feed_id: UUID, upload_id: UUID) -> str:
    return f"{SIGHT_PATH_PREFIX}/{user_id}/{feed_id}/{upload_id}{SIGHT_OBJECT_EXT}"


def assert_server_object_path(
    object_path: str, *, user_id: UUID, feed_id: UUID, upload_id: UUID
) -> None:
    expected = sight_object_path(user_id, feed_id, upload_id)
    if object_path != expected:
        raise InvalidSightUpload("object path must be server generated")
    parts = object_path.split("/")
    if len(parts) != 4 or parts[0] != SIGHT_PATH_PREFIX or not parts[3].endswith(SIGHT_OBJECT_EXT):
        raise InvalidSightUpload("object path must have four segments")


def validate_upload_constraints(*, mime_type: str, size_bytes: int, sha256: str) -> None:
    if mime_type != ALLOWED_MIME:
        raise InvalidSightUpload("mime type is not allowed")
    if size_bytes < MIN_SIZE_BYTES or size_bytes > MAX_SIZE_BYTES:
        raise InvalidSightUpload("size is outside 1..5 MiB")
    if SHA256_RE.fullmatch(sha256) is None:
        raise InvalidSightUpload("sha256 must be 64 lowercase hex")


def sight_upload_request_hash(
    *, feed_id: UUID, mime_type: str, size_bytes: int, sha256: str
) -> str:
    canonical = json.dumps(
        {
            "feed_id": str(feed_id),
            "mime_type": mime_type,
            "sha256": sha256,
            "size_bytes": size_bytes,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def expires_at_for(now: datetime) -> datetime:
    return now + UPLOAD_TTL


def sign_put_url(
    *,
    bucket: str,
    object_path: str,
    expires_at: datetime,
    secret: bytes,
) -> str:
    exp = int(expires_at.timestamp())
    material = f"{PUT_METHOD}\n{bucket}\n{object_path}\n{exp}".encode()
    sig = hmac.new(secret, material, hashlib.sha256).hexdigest()
    encoded_path = quote(object_path, safe="/")
    return f"{_SIGNED_PUT_ORIGIN}/{bucket}/{encoded_path}?exp={exp}&sig={sig}"


def verify_signed_put_url(url: str, *, secret: bytes, now: datetime) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "kelin.invalid":
        raise InvalidSightUpload("put url is not a kelin object url")
    prefix = "/object/"
    if not parsed.path.startswith(prefix):
        raise InvalidSightUpload("put url path is invalid")
    rest = parsed.path[len(prefix) :]
    bucket, sep, raw_path = rest.partition("/")
    if not sep or not bucket or not raw_path:
        raise InvalidSightUpload("put url path is invalid")
    object_path = unquote(raw_path)
    query = parse_qs(parsed.query)
    exp_values = query.get("exp", [])
    sig_values = query.get("sig", [])
    if len(exp_values) != 1 or len(sig_values) != 1:
        raise InvalidSightUpload("put url signature is invalid")
    try:
        exp = int(exp_values[0])
    except ValueError as exc:
        raise InvalidSightUpload("put url signature is invalid") from exc
    if now.timestamp() > exp:
        raise InvalidSightUpload("put url expired")
    material = f"{PUT_METHOD}\n{bucket}\n{object_path}\n{exp}".encode()
    expected = hmac.new(secret, material, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig_values[0], expected):
        raise InvalidSightUpload("put url signature is invalid")
    return bucket, object_path


@dataclass(frozen=True, slots=True)
class ObjectInspection:
    size_bytes: int
    sha256: str
    magic_ok: bool


def inspect_object_bytes(body: bytes) -> ObjectInspection:
    return ObjectInspection(
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        magic_ok=body.startswith(JPEG_MAGIC),
    )


def object_mismatch_reason(
    inspection: ObjectInspection, *, expected_size: int, expected_sha256: str
) -> str | None:
    if not inspection.magic_ok:
        return "magic_mismatch"
    if inspection.size_bytes != expected_size:
        return "size_mismatch"
    if inspection.sha256 != expected_sha256:
        return "hash_mismatch"
    return None


def jpeg_fixture_bytes(size: int, *, fill: bytes = b"\x00") -> bytes:
    if size < len(JPEG_MAGIC) or size > MAX_SIZE_BYTES:
        raise InvalidSightUpload("size is outside 1..5 MiB")
    if len(fill) != 1:
        raise InvalidSightUpload("fill must be one byte")
    return JPEG_MAGIC + fill * (size - len(JPEG_MAGIC))


def due_for_cleanup(
    *,
    status: str,
    expires_at: datetime,
    created_at: datetime,
    object_deleted_at: datetime | None,
    now: datetime,
) -> bool:
    return (
        cleanup_reason(
            status=status,
            expires_at=expires_at,
            created_at=created_at,
            object_deleted_at=object_deleted_at,
            now=now,
        )
        is not None
    )


def cleanup_reason(
    *,
    status: str,
    expires_at: datetime,
    created_at: datetime,
    object_deleted_at: datetime | None,
    now: datetime,
) -> str | None:
    if object_deleted_at is not None:
        return None
    if status in {"consumed", "rejected"}:
        return status
    if status == "expired" or (status in ACTIVE_UPLOAD_STATUSES and expires_at <= now):
        return "expired"
    if created_at + ORIGINAL_RETENTION <= now:
        return "retention_sla"
    return None


def upload_cleanup_dedupe_key(upload_id: UUID) -> str:
    return f"upload-cleanup:{upload_id}"
