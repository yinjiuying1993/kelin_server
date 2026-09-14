from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.domain.sight_upload import jpeg_fixture_bytes, sign_put_url
from app.integrations.storage import (
    PrivateSightStorage,
    StorageAccessDenied,
    StorageListDenied,
    StoragePutRejected,
)
from pytest import raises

NOW = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
SECRET = b"kelin-dev-cursor-hmac"


def _url_for(path: str) -> str:
    return sign_put_url(
        bucket="kelin-sight",
        object_path=path,
        expires_at=NOW + timedelta(minutes=10),
        secret=SECRET,
    )


def test_client_cannot_list_read_or_delete_and_put_is_path_bound() -> None:
    store = PrivateSightStorage()
    owner_a = uuid4()
    owner_b = uuid4()
    path_a = f"sight-temp/{owner_a}/{uuid4()}/{uuid4()}.jpg"
    path_b = f"sight-temp/{owner_b}/{uuid4()}/{uuid4()}.jpg"
    body = jpeg_fixture_bytes(1024)
    store.client_put(
        _url_for(path_a),
        {"content-type": "image/jpeg"},
        body,
        secret=SECRET,
        now=NOW,
    )
    with raises(StorageAccessDenied):
        store.client_get("kelin-sight", path_a)
    with raises(StorageAccessDenied):
        store.client_get("kelin-sight", path_b)
    with raises(StorageAccessDenied):
        store.client_delete("kelin-sight", path_a)
    with raises(StorageListDenied):
        store.client_list("kelin-sight", f"sight-temp/{owner_b}/")
    with raises(StorageListDenied):
        store.service_list("kelin-sight", f"sight-temp/{owner_a}/")
    tampered = _url_for(path_a).replace(str(owner_a), str(owner_b))
    with raises(StoragePutRejected):
        store.client_put(
            tampered,
            {"content-type": "image/jpeg"},
            body,
            secret=SECRET,
            now=NOW,
        )
    assert store.service_get("kelin-sight", path_a) == body
    with raises(StoragePutRejected):
        store.client_put(
            _url_for(path_b),
            {"content-type": "image/jpeg"},
            b"\x89PNG" + b"\x00" * 1020,
            secret=SECRET,
            now=NOW,
        )
    assert store.service_delete("kelin-sight", path_a) is True
    assert store.service_delete("kelin-sight", path_b) is False
