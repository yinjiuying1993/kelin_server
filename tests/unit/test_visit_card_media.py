"""Visit-card bytes stay on the signed object route."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from app.core.config import Settings
from app.domain.sight_upload import expires_at_for, sign_put_url
from app.domain.speech import m4a_fixture_bytes, sign_playback_url
from app.domain.visit_card import CARD_MEDIA_MAX_BYTES, VISIT_CARD_BUCKET
from app.main import create_app
from fastapi.testclient import TestClient

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
_KEY = "visit-cards/00000000-0000-4000-8000-0000000000aa/00000000-0000-4000-8000-0000000000bb-image.png"
_VOICE = "visit-cards/00000000-0000-4000-8000-0000000000aa/00000000-0000-4000-8000-0000000000bb-voice.m4a"


def _client() -> tuple[TestClient, bytes]:
    application = create_app(Settings(app_env="test"))
    return TestClient(application), Settings(app_env="test").cursor_signing_key()


def _put(client: TestClient, secret: bytes, key: str, body: bytes, content_type: str, *, now: datetime | None = None):
    stamp = now or datetime.now(UTC)
    url = sign_put_url(
        bucket=VISIT_CARD_BUCKET,
        object_path=key,
        expires_at=expires_at_for(stamp),
        secret=secret,
    )
    parsed = urlparse(url)
    return client.put(
        f"{parsed.path}?{parsed.query}",
        content=body,
        headers={"content-type": content_type},
    )


def test_put_then_get_png_and_m4a() -> None:
    client, secret = _client()
    stored = _put(client, secret, _KEY, _PNG, "image/png")
    assert stored.status_code == 204
    now = datetime.now(UTC)
    playback = sign_playback_url(
        bucket=VISIT_CARD_BUCKET,
        object_path=_KEY,
        expires_at=now + timedelta(minutes=10),
        secret=secret,
    )
    parsed = urlparse(playback)
    played = client.get(f"{parsed.path}?{parsed.query}")
    assert played.status_code == 200
    assert played.headers["content-type"].startswith("image/png")
    assert played.content == _PNG

    voice = m4a_fixture_bytes(duration_ms=800)
    assert _put(client, secret, _VOICE, voice, "audio/mp4").status_code == 204
    voice_url = sign_playback_url(
        bucket=VISIT_CARD_BUCKET,
        object_path=_VOICE,
        expires_at=now + timedelta(minutes=10),
        secret=secret,
    )
    voice_parsed = urlparse(voice_url)
    heard = client.get(f"{voice_parsed.path}?{voice_parsed.query}")
    assert heard.status_code == 200
    assert heard.headers["content-type"].startswith("audio/mp4")
    assert heard.content == voice


def test_put_rejects_bad_signature_magic_and_foreign_bucket() -> None:
    client, secret = _client()
    bad = client.put(
        f"/object/{VISIT_CARD_BUCKET}/{_KEY}?exp=1&sig=deadbeef",
        content=_PNG,
        headers={"content-type": "image/png"},
    )
    assert bad.status_code == 404
    assert bad.json()["error"]["code"] == "NOT_FOUND"

    mismatch = _put(client, secret, _KEY, b"\xff\xd8\xff" + b"\x00" * 16, "image/png")
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "CARD_IMAGE_REJECTED"

    voice = _put(client, secret, _VOICE, b"not-an-m4a-file", "audio/mp4")
    assert voice.status_code == 422
    assert voice.json()["error"]["code"] == "CARD_VOICE_REJECTED"

    huge = b"\x89PNG\r\n\x1a\n" + b"\x00" * CARD_MEDIA_MAX_BYTES
    oversized = _put(client, secret, _KEY, huge, "image/png")
    assert oversized.status_code == 422
    assert oversized.json()["error"]["code"] == "CARD_IMAGE_REJECTED"

    foreign = sign_put_url(
        bucket="kelin-tts",
        object_path="tts/nope.m4a",
        expires_at=expires_at_for(datetime.now(UTC)),
        secret=secret,
    )
    parsed = urlparse(foreign)
    denied = client.put(
        f"{parsed.path}?{parsed.query}",
        content=m4a_fixture_bytes(duration_ms=800),
        headers={"content-type": "audio/mp4"},
    )
    assert denied.status_code == 404
    application = client.app
    assert not any(path.startswith("/object/") for path in application.openapi().get("paths", {}))
