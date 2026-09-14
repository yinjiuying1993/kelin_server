from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.domain.sight_upload import (
    ORIGINAL_RETENTION,
    InvalidSightUpload,
    cleanup_reason,
    inspect_object_bytes,
    jpeg_fixture_bytes,
    object_mismatch_reason,
    sign_put_url,
    verify_signed_put_url,
)
from pytest import raises

NOW = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)


def test_jpeg_magic_and_hash_mismatch_reasons() -> None:
    body = jpeg_fixture_bytes(1024)
    inspection = inspect_object_bytes(body)
    assert inspection.magic_ok is True
    assert inspection.size_bytes == 1024
    assert (
        object_mismatch_reason(inspection, expected_size=1024, expected_sha256=inspection.sha256)
        is None
    )
    png = b"\x89PNG" + b"\x00" * 1020
    assert inspect_object_bytes(png).magic_ok is False
    assert (
        object_mismatch_reason(
            inspect_object_bytes(png), expected_size=1024, expected_sha256=inspection.sha256
        )
        == "magic_mismatch"
    )
    other = jpeg_fixture_bytes(1024, fill=b"\x01")
    other_ins = inspect_object_bytes(other)
    assert (
        object_mismatch_reason(other_ins, expected_size=1024, expected_sha256=inspection.sha256)
        == "hash_mismatch"
    )
    assert (
        object_mismatch_reason(inspection, expected_size=2048, expected_sha256=inspection.sha256)
        == "size_mismatch"
    )


def test_cleanup_due_covers_expiry_consumed_and_24h_sla() -> None:
    created = NOW
    expires = NOW + timedelta(minutes=10)
    assert (
        cleanup_reason(
            status="issued",
            expires_at=expires,
            created_at=created,
            object_deleted_at=None,
            now=NOW,
        )
        is None
    )
    assert (
        cleanup_reason(
            status="issued",
            expires_at=expires,
            created_at=created,
            object_deleted_at=None,
            now=expires,
        )
        == "expired"
    )
    assert (
        cleanup_reason(
            status="consumed",
            expires_at=expires,
            created_at=created,
            object_deleted_at=None,
            now=NOW,
        )
        == "consumed"
    )
    assert (
        cleanup_reason(
            status="issued",
            expires_at=NOW + timedelta(hours=2),
            created_at=NOW - ORIGINAL_RETENTION,
            object_deleted_at=None,
            now=NOW,
        )
        == "retention_sla"
    )
    assert (
        cleanup_reason(
            status="expired",
            expires_at=expires,
            created_at=created,
            object_deleted_at=NOW,
            now=NOW,
        )
        is None
    )


def test_signed_put_url_rejects_tampered_path() -> None:
    secret = b"kelin-dev-cursor-hmac"
    path = f"sight-temp/{uuid4()}/{uuid4()}/{uuid4()}.jpg"
    url = sign_put_url(
        bucket="kelin-sight",
        object_path=path,
        expires_at=NOW + timedelta(minutes=10),
        secret=secret,
    )
    bucket, object_path = verify_signed_put_url(url, secret=secret, now=NOW)
    assert bucket == "kelin-sight"
    assert object_path == path
    tampered = url.replace(str(path.split("/")[1]), str(uuid4()))
    with raises(InvalidSightUpload):
        verify_signed_put_url(tampered, secret=secret, now=NOW)
