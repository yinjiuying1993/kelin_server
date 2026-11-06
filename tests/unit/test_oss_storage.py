"""Visit-card, sight, and TTS bytes use Aliyun OSS when dev/prod is configured."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from app.core.config import Settings
from app.domain.sight_upload import expires_at_for, sign_put_url
from app.domain.visit_card import VISIT_CARD_BUCKET
from app.integrations.oss import (
    AliyunOssBackend,
    authorization_header,
    backend_from_settings,
    string_to_sign,
)
from app.integrations.storage import (
    PrivateVisitCardStorage,
    StorageObjectMissing,
    StorageUnavailable,
)
from app.main import create_app
from pydantic import SecretStr
from pytest import raises

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
_KEY = "visit-cards/00000000-0000-4000-8000-0000000000aa/00000000-0000-4000-8000-0000000000bb-image.png"
_ENDPOINT = "https://oss-cn-hangzhou.aliyuncs.com"
_BUCKET = "kelin-media"
_KEY_ID = "test-access-key"
_SECRET = "test-secret-value"


class _Box:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], tuple[str, bytes]] = {}

    def put(self, bucket: str, object_path: str, body: bytes, *, content_type: str) -> None:
        self.items[(bucket, object_path)] = (content_type, bytes(body))

    def get(self, bucket: str, object_path: str) -> bytes:
        try:
            return self.items[(bucket, object_path)][1]
        except KeyError as exc:
            raise StorageObjectMissing("object is missing") from exc

    def delete(self, bucket: str, object_path: str) -> bool:
        return self.items.pop((bucket, object_path), None) is not None

    def list_prefix(self, bucket: str, prefix: str) -> list[str]:
        return sorted(
            path for (key_bucket, path) in self.items if key_bucket == bucket and path.startswith(prefix)
        )

    def contains(self, bucket: str, object_path: str) -> bool:
        return (bucket, object_path) in self.items


def test_string_to_sign_is_aliyun_v1() -> None:
    signed = string_to_sign(
        method="PUT",
        content_type="image/png",
        date="Thu, 17 Nov 2005 18:49:58 GMT",
        canonical=f"/{_BUCKET}/{VISIT_CARD_BUCKET}/{_KEY}",
    )
    assert signed == (
        "PUT\n\nimage/png\nThu, 17 Nov 2005 18:49:58 GMT\n"
        f"/{_BUCKET}/{VISIT_CARD_BUCKET}/{_KEY}"
    )
    header = authorization_header(
        method="GET",
        content_type="",
        date="Thu, 17 Nov 2005 18:49:58 GMT",
        canonical=f"/{_BUCKET}/{VISIT_CARD_BUCKET}/{_KEY}",
        access_key_id=_KEY_ID,
        access_key_secret=_SECRET,
    )
    assert header.startswith(f"OSS {_KEY_ID}:")
    assert _SECRET not in header


def test_visit_card_put_uses_injected_backend() -> None:
    box = _Box()
    store = PrivateVisitCardStorage(box)
    now = datetime(2026, 10, 22, tzinfo=UTC)
    url = sign_put_url(
        bucket=VISIT_CARD_BUCKET,
        object_path=_KEY,
        expires_at=expires_at_for(now),
        secret=b"kelin-dev-cursor-hmac",
    )
    store.client_put(
        url,
        {"content-type": "image/png"},
        _PNG,
        secret=b"kelin-dev-cursor-hmac",
        now=now,
    )
    assert box.items[(VISIT_CARD_BUCKET, _KEY)] == ("image/png", _PNG)
    assert store.service_get(VISIT_CARD_BUCKET, _KEY) == _PNG
    with raises(StorageObjectMissing):
        PrivateVisitCardStorage().service_get(VISIT_CARD_BUCKET, _KEY)


def test_oss_put_get_delete_and_list_round_trip() -> None:
    saved: dict[str, bytes] = {}
    voice_key = _KEY.replace("-image.png", "-voice.m4a")

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.lstrip("/")
        params = {name: value for name, value in request.url.params.multi_items()}
        resource = f"/{_BUCKET}/{key}" if key else f"/{_BUCKET}/"
        if params:
            query = "&".join(
                name if params[name] == "" else f"{name}={params[name]}" for name in sorted(params)
            )
            resource = f"{resource}?{query}"
        content_type = request.headers.get("content-type", "")
        expected = authorization_header(
            method=request.method,
            content_type=content_type,
            date=request.headers["date"],
            canonical=resource,
            access_key_id=_KEY_ID,
            access_key_secret=_SECRET,
        )
        if request.headers["authorization"] != expected:
            return httpx.Response(403)
        if request.url.host != f"{_BUCKET}.oss-cn-hangzhou.aliyuncs.com":
            return httpx.Response(403)
        if request.method == "PUT":
            saved[key] = request.content
            return httpx.Response(200)
        if request.method in {"GET", "HEAD"} and "prefix" in params:
            matched = [name for name in sorted(saved) if name.startswith(params["prefix"])]
            body = "<ListBucketResult>" + "".join(
                f"<Contents><Key>{name}</Key></Contents>" for name in matched
            )
            body += "<IsTruncated>false</IsTruncated></ListBucketResult>"
            return httpx.Response(200, content=body.encode())
        if request.method == "HEAD":
            return httpx.Response(200 if key in saved else 404)
        if request.method == "GET":
            if key not in saved:
                return httpx.Response(404)
            return httpx.Response(200, content=saved[key])
        if request.method == "DELETE":
            saved.pop(key, None)
            return httpx.Response(204)
        return httpx.Response(500)

    backend = AliyunOssBackend(
        endpoint=_ENDPOINT,
        bucket=_BUCKET,
        access_key_id=_KEY_ID,
        access_key_secret=_SECRET,
        image_prefix="kelin/images",
        voice_prefix="kelin/voice",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    backend.put(VISIT_CARD_BUCKET, _KEY, _PNG, content_type="image/png")
    backend.put(VISIT_CARD_BUCKET, voice_key, b"voice", content_type="audio/mp4")
    assert f"kelin/images/{_KEY}" in saved
    assert f"kelin/voice/{voice_key}" in saved
    assert backend.get(VISIT_CARD_BUCKET, _KEY) == _PNG
    assert backend.get(VISIT_CARD_BUCKET, voice_key) == b"voice"
    listed = backend.list_prefix(VISIT_CARD_BUCKET, "visit-cards/")
    assert listed == sorted([_KEY, voice_key])
    assert backend.delete(VISIT_CARD_BUCKET, _KEY) is True
    assert backend.delete(VISIT_CARD_BUCKET, voice_key) is True
    assert backend.delete(VISIT_CARD_BUCKET, _KEY) is False
    with raises(StorageObjectMissing):
        backend.get(VISIT_CARD_BUCKET, _KEY)


def test_oss_transport_error_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.ConnectError("down")

    backend = AliyunOssBackend(
        endpoint=_ENDPOINT,
        bucket=_BUCKET,
        access_key_id=_KEY_ID,
        access_key_secret=_SECRET,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with raises(StorageUnavailable):
        backend.get(VISIT_CARD_BUCKET, _KEY)


def test_test_env_keeps_memory_and_presence_hides_secrets() -> None:
    secret = SecretStr(_SECRET)
    settings = Settings(
        app_env="test",
        oss_endpoint=_ENDPOINT,
        oss_bucket=_BUCKET,
        oss_access_key_id=SecretStr(_KEY_ID),
        oss_access_key_secret=secret,
    )
    assert backend_from_settings(settings) is None
    presence = settings.config_presence()
    dumped = str(presence)
    assert presence["oss_access_key_secret"] == "set"
    assert presence["oss_bucket"] == "set"
    assert _SECRET not in dumped
    assert _KEY_ID not in dumped
    assert _BUCKET not in dumped
    application = create_app(settings)
    assert application.state.object_backend is None


def test_dev_without_a_full_oss_config_stays_in_memory() -> None:
    settings = Settings(
        app_env="dev",
        oss_endpoint=_ENDPOINT,
        oss_bucket=_BUCKET,
        oss_access_key_id=None,
        oss_access_key_secret=None,
    )
    assert backend_from_settings(settings) is None
    empty = Settings.model_validate(
        {
            "app_env": "dev",
            "oss_endpoint": "",
            "oss_bucket": "",
            "oss_access_key_id": "",
            "oss_access_key_secret": "",
        }
    )
    assert empty.oss_access_key_secret is None
    assert empty.config_presence()["oss_endpoint"] == "unset"


def test_dev_with_oss_config_uses_one_backend() -> None:
    settings = Settings(
        app_env="dev",
        oss_endpoint=_ENDPOINT,
        oss_bucket=_BUCKET,
        oss_access_key_id=SecretStr(_KEY_ID),
        oss_access_key_secret=SecretStr(_SECRET),
    )
    application = create_app(settings)
    backend = application.state.object_backend
    assert isinstance(backend, AliyunOssBackend)
    assert application.state.visit_card_storage._blobs is backend
    assert application.state.tts_storage._blobs is backend
    assert application.state.sight_storage._blobs is backend
    backend.close()
