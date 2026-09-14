from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import gettempdir
from urllib.parse import urlparse
from uuid import UUID

from app.core.config import Settings
from app.domain.quota import ASR_DAILY_LIMIT, TTS_DAILY_LIMIT
from app.domain.speech import (
    ALLOWED_AUDIO_MIME,
    ASR_MAX_DURATION_MS,
    ASR_MAX_SIZE_BYTES,
    ASR_TEMP_PREFIX,
    TTS_BUCKET,
    TTS_CACHE_TTL,
    TTS_MAX_CHARS,
    TTS_OUTPUT_MIME,
    TTS_PLAYBACK_TTL,
    TTS_VOICE_DEFAULT,
    AudioInspection,
    EmptyTranscript,
    InvalidPlaybackUrl,
    InvalidSpeech,
    TtsCacheRecord,
    asr_quota_amount,
    audio_sha256,
    inspect_m4a_bytes,
    m4a_fixture_bytes,
    playback_expires_at,
    require_transcript_text,
    require_tts_text,
    require_voice_profile,
    sign_playback_url,
    temporary_asr_audio,
    transcribe_request_hash,
    tts_cache_key,
    tts_object_path,
    tts_quota_amount,
    validate_asr_upload,
    verify_signed_playback_url,
)
from app.integrations.storage import (
    PrivateTtsStorage,
    StorageAccessDenied,
    StorageListDenied,
    StorageObjectMissing,
)
from app.main import create_app
from fastapi.testclient import TestClient
from pytest import raises


def test_m4a_sample_locks_mime_size_and_duration() -> None:
    body = m4a_fixture_bytes(duration_ms=1200)
    inspected = validate_asr_upload(mime_type="audio/mp4", body=body)
    assert isinstance(inspected, AudioInspection)
    assert inspected.duration_ms == 1200
    assert inspected.size_bytes <= ASR_MAX_SIZE_BYTES
    assert "M4A " in inspected.brands
    for mime in ALLOWED_AUDIO_MIME:
        validate_asr_upload(mime_type=f"{mime}; codecs=mp4a.40.2", body=body)


def test_invalid_mime_wav_and_jpeg_are_rejected() -> None:
    body = m4a_fixture_bytes(duration_ms=800)
    with raises(InvalidSpeech, match="mime"):
        validate_asr_upload(mime_type="audio/wav", body=body)
    with raises(InvalidSpeech, match="mime"):
        validate_asr_upload(mime_type="image/jpeg", body=body)
    with raises(InvalidSpeech, match="container"):
        inspect_m4a_bytes(b"RIFF" + b"\x00" * 8 + b"WAVEfmt ")
    with raises(InvalidSpeech, match="container"):
        inspect_m4a_bytes(b"\xff\xd8\xff" + b"\x00" * 32)


def test_duration_and_size_bounds() -> None:
    ok = inspect_m4a_bytes(m4a_fixture_bytes(duration_ms=ASR_MAX_DURATION_MS))
    assert ok.duration_ms == ASR_MAX_DURATION_MS
    with raises(InvalidSpeech, match="30 s"):
        inspect_m4a_bytes(m4a_fixture_bytes(duration_ms=ASR_MAX_DURATION_MS + 1))
    with raises(InvalidSpeech, match="5 MiB"):
        inspect_m4a_bytes(b"\x00" * (ASR_MAX_SIZE_BYTES + 1))


def test_empty_transcript_is_a_stable_error() -> None:
    assert require_transcript_text(" 你好 ") == "你好"
    with raises(EmptyTranscript):
        require_transcript_text("   ")
    with raises(EmptyTranscript):
        require_transcript_text("")


def test_tts_cache_hit_does_not_count_quota() -> None:
    assert ASR_DAILY_LIMIT == 60
    assert TTS_DAILY_LIMIT == 20
    assert asr_quota_amount() == 1
    assert tts_quota_amount(cache_hit=False) == 1
    assert tts_quota_amount(cache_hit=True) == 0
    assert TTS_CACHE_TTL == timedelta(hours=24)
    assert TTS_PLAYBACK_TTL == timedelta(minutes=10)
    assert TTS_OUTPUT_MIME == "audio/mp4"
    message_id = UUID("00000000-0000-4000-8000-000000000041")
    owner_id = UUID("00000000-0000-4000-8000-000000000001")
    key = tts_cache_key(
        message_id=message_id,
        voice_profile=TTS_VOICE_DEFAULT,
        model_alias="tts-default",
    )
    assert key == f"{message_id}:default:tts-default"
    path = tts_object_path(
        owner_id=owner_id, message_id=message_id, voice_profile=TTS_VOICE_DEFAULT
    )
    assert str(owner_id) in path
    assert path.startswith("tts/")
    other = UUID("00000000-0000-4000-8000-000000000099")
    assert (
        tts_object_path(owner_id=other, message_id=message_id, voice_profile=TTS_VOICE_DEFAULT)
        != path
    )
    with raises(InvalidSpeech, match="voice_profile"):
        require_voice_profile("warm")
    with raises(InvalidSpeech, match="200"):
        require_tts_text("字" * (TTS_MAX_CHARS + 1))
    assert len(require_tts_text("字" * TTS_MAX_CHARS)) == TTS_MAX_CHARS


def test_temporary_asr_audio_is_gone_after_success_and_error() -> None:
    body = m4a_fixture_bytes(duration_ms=800)
    with temporary_asr_audio(body) as path:
        assert path.exists()
        assert path.name.startswith(ASR_TEMP_PREFIX)
        held = path
    assert not held.exists()
    with raises(RuntimeError, match="boom"):
        with temporary_asr_audio(body) as path:
            held = path
            raise RuntimeError("boom")
    assert not held.exists()
    leftovers_dir = Path(gettempdir()) / "kelin-asr"
    leftovers = list(leftovers_dir.glob(f"{ASR_TEMP_PREFIX}*")) if leftovers_dir.exists() else []
    assert leftovers == []


def test_signed_playback_url_expires_and_keeps_owner_path() -> None:
    secret = Settings(app_env="test").cursor_signing_key()
    now = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    owner_id = UUID("00000000-0000-4000-8000-000000000001")
    message_id = UUID("00000000-0000-4000-8000-000000000041")
    object_path = tts_object_path(
        owner_id=owner_id, message_id=message_id, voice_profile=TTS_VOICE_DEFAULT
    )
    url = sign_playback_url(
        bucket=TTS_BUCKET,
        object_path=object_path,
        expires_at=playback_expires_at(now),
        secret=secret,
    )
    assert url.startswith(f"https://kelin.invalid/object/{TTS_BUCKET}/tts/{owner_id}/")
    bucket, verified_path = verify_signed_playback_url(url, secret=secret, now=now)
    assert bucket == TTS_BUCKET
    assert verified_path == object_path
    with raises(InvalidPlaybackUrl, match="expired"):
        verify_signed_playback_url(
            url, secret=secret, now=now + TTS_PLAYBACK_TTL + timedelta(seconds=1)
        )
    tampered = url[:-4] + "abcd"
    with raises(InvalidPlaybackUrl, match="signature"):
        verify_signed_playback_url(tampered, secret=secret, now=now)


def test_tts_cache_expire_due_deletes_object_and_isolates_owner() -> None:
    storage = PrivateTtsStorage()
    owner_id = UUID("00000000-0000-4000-8000-000000000001")
    other = UUID("00000000-0000-4000-8000-000000000099")
    message_id = UUID("00000000-0000-4000-8000-000000000041")
    object_path = tts_object_path(
        owner_id=owner_id, message_id=message_id, voice_profile=TTS_VOICE_DEFAULT
    )
    created = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    body = m4a_fixture_bytes(duration_ms=800)
    storage.service_put(TTS_BUCKET, object_path, body)
    record = TtsCacheRecord(
        owner_id=owner_id,
        message_id=message_id,
        voice_profile=TTS_VOICE_DEFAULT,
        model_alias="tts",
        object_path=object_path,
        duration_ms=800,
        created_at=created,
        provider_request_id="tts-1",
    )
    storage.put_cache(record)
    cache_key = tts_cache_key(
        message_id=message_id, voice_profile=TTS_VOICE_DEFAULT, model_alias="tts"
    )
    assert storage.get_cache(owner_id, cache_key, now=created) is not None
    assert storage.get_cache(other, cache_key, now=created) is None
    with raises(StorageAccessDenied):
        storage.client_get(TTS_BUCKET, object_path)
    with raises(StorageListDenied):
        storage.service_list(TTS_BUCKET, "tts/")
    assert storage.expire_due(now=created + TTS_CACHE_TTL + timedelta(seconds=1)) == 1
    with raises(StorageObjectMissing):
        storage.service_get(TTS_BUCKET, object_path)
    assert storage.get_cache(owner_id, cache_key, now=created) is None


def test_playback_get_streams_m4a_and_rejects_bad_signature() -> None:
    application = create_app(Settings(app_env="test"))
    storage = application.state.tts_storage
    assert isinstance(storage, PrivateTtsStorage)
    secret = Settings(app_env="test").cursor_signing_key()
    owner_id = UUID("00000000-0000-4000-8000-000000000001")
    message_id = UUID("00000000-0000-4000-8000-000000000041")
    object_path = tts_object_path(
        owner_id=owner_id, message_id=message_id, voice_profile=TTS_VOICE_DEFAULT
    )
    body = m4a_fixture_bytes(duration_ms=800)
    storage.service_put(TTS_BUCKET, object_path, body)
    now = datetime.now(UTC)
    url = sign_playback_url(
        bucket=TTS_BUCKET,
        object_path=object_path,
        expires_at=playback_expires_at(now),
        secret=secret,
    )
    parsed = urlparse(url)
    client = TestClient(application)
    ok = client.get(f"{parsed.path}?{parsed.query}")
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("audio/mp4")
    assert ok.content == body
    bad = client.get(f"{parsed.path}?exp=1&sig=deadbeef")
    assert bad.status_code == 404
    expired = sign_playback_url(
        bucket=TTS_BUCKET,
        object_path=object_path,
        expires_at=now - timedelta(seconds=1),
        secret=secret,
    )
    expired_parsed = urlparse(expired)
    gone = client.get(f"{expired_parsed.path}?{expired_parsed.query}")
    assert gone.status_code == 404
    spec_paths = set(application.openapi().get("paths", {}))
    assert not any(path.startswith("/object/") for path in spec_paths)


def test_transcribe_request_hash_is_stable() -> None:
    body = m4a_fixture_bytes(duration_ms=800)
    digest = audio_sha256(body)
    first = transcribe_request_hash(sha256=digest, mime_type="audio/mp4", size_bytes=len(body))
    again = transcribe_request_hash(sha256=digest, mime_type="audio/mp4", size_bytes=len(body))
    assert first == again
    other = transcribe_request_hash(sha256=digest, mime_type="audio/m4a", size_bytes=len(body))
    assert other != first
