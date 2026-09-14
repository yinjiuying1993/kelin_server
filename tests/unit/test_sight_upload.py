from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.sight_upload import (
    MAX_SIZE_BYTES,
    InvalidSightUpload,
    assert_server_object_path,
    sight_object_path,
    sign_put_url,
    validate_upload_constraints,
)
from pytest import raises


def test_object_path_is_four_segments_and_server_owned() -> None:
    user_id = UUID("00000000-0000-4000-8000-000000000001")
    feed_id = UUID("00000000-0000-4000-8000-000000000002")
    upload_id = UUID("00000000-0000-4000-8000-000000000003")
    path = sight_object_path(user_id, feed_id, upload_id)
    assert path == (
        "sight-temp/00000000-0000-4000-8000-000000000001/"
        "00000000-0000-4000-8000-000000000002/"
        "00000000-0000-4000-8000-000000000003.jpg"
    )
    assert_server_object_path(path, user_id=user_id, feed_id=feed_id, upload_id=upload_id)
    with raises(InvalidSightUpload):
        assert_server_object_path(
            "owners/custom.jpg", user_id=user_id, feed_id=feed_id, upload_id=upload_id
        )


def test_upload_constraints_lock_jpeg_size_and_sha256() -> None:
    validate_upload_constraints(mime_type="image/jpeg", size_bytes=1, sha256="a" * 64)
    validate_upload_constraints(mime_type="image/jpeg", size_bytes=MAX_SIZE_BYTES, sha256="b" * 64)
    with raises(InvalidSightUpload):
        validate_upload_constraints(mime_type="image/png", size_bytes=1024, sha256="a" * 64)
    with raises(InvalidSightUpload):
        validate_upload_constraints(mime_type="image/jpeg", size_bytes=0, sha256="a" * 64)
    with raises(InvalidSightUpload):
        validate_upload_constraints(
            mime_type="image/jpeg", size_bytes=MAX_SIZE_BYTES + 1, sha256="a" * 64
        )
    with raises(InvalidSightUpload):
        validate_upload_constraints(mime_type="image/jpeg", size_bytes=1024, sha256="A" * 64)


def test_signed_put_url_is_not_a_service_role() -> None:
    path = sight_object_path(uuid4(), uuid4(), uuid4())
    url = sign_put_url(
        bucket="kelin-sight",
        object_path=path,
        expires_at=datetime(2026, 9, 8, 0, 10, tzinfo=UTC),
        secret=b"kelin-dev-cursor-hmac",
    )
    assert url.startswith("https://kelin.invalid/object/kelin-sight/")
    assert "service_role" not in url.lower()
    assert "eyj" not in url.lower()
    assert "PUT" not in url
    assert path in url
