"""ASR/TTS contract constants, m4a container probe, and cache billing. Spec §§10.4, 10.5."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Literal
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import UUID

from app.domain.quota import ASR_DAILY_LIMIT, TTS_DAILY_LIMIT

ASR_MAX_SIZE_BYTES = 5 * 1024 * 1024
ASR_MAX_DURATION_MS = 30_000
ASR_MIN_DURATION_MS = 1
TTS_MAX_CHARS = 200
TTS_VOICE_DEFAULT: Final[Literal["default"]] = "default"
TTS_OUTPUT_MIME: Final[Literal["audio/mp4"]] = "audio/mp4"
TTS_CACHE_TTL = timedelta(hours=24)
TTS_PLAYBACK_TTL = timedelta(minutes=10)
ASR_TRANSCRIBE_OPERATION = "speech.transcribe"
TTS_SYNTHESIZE_OPERATION = "speech.synthesize"
ASR_TEMP_PREFIX = "kelin-asr-"
TTS_BUCKET = "kelin-tts"
TTS_PATH_PREFIX = "tts"
TTS_GET_METHOD: Final[Literal["GET"]] = "GET"
_SIGNED_OBJECT_ORIGIN = "https://kelin.invalid/object"
ALLOWED_AUDIO_MIME: Final[frozenset[str]] = frozenset(
    {
        "audio/mp4",
        "audio/m4a",
        "audio/x-m4a",
        "audio/aac",
    }
)
ALLOWED_FTYP_BRANDS: Final[frozenset[bytes]] = frozenset(
    {b"M4A ", b"M4B ", b"mp41", b"mp42", b"isom", b"iso2"}
)


class InvalidSpeech(ValueError):
    """MIME, size, container, duration, voice, or TTS text failed the locked contract."""


class EmptyTranscript(ValueError):
    """Provider returned blank ASR text. Maps to ASR_EMPTY_RESULT."""


class InvalidPlaybackUrl(ValueError):
    """Signed TTS GET URL failed host, signature, path, or expiry checks."""


@dataclass(frozen=True, slots=True)
class AudioInspection:
    size_bytes: int
    duration_ms: int
    brands: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TtsCacheRecord:
    owner_id: UUID
    message_id: UUID
    voice_profile: str
    model_alias: str
    object_path: str
    duration_ms: int
    created_at: datetime
    provider_request_id: str | None


def normalize_audio_mime(value: str | None) -> str:
    raw = (value or "").split(";", 1)[0].strip().lower()
    if raw not in ALLOWED_AUDIO_MIME:
        raise InvalidSpeech("mime type is not allowed")
    return raw


def inspect_m4a_bytes(body: bytes) -> AudioInspection:
    if not body:
        raise InvalidSpeech("audio is empty")
    if len(body) > ASR_MAX_SIZE_BYTES:
        raise InvalidSpeech("audio exceeds 5 MiB")
    if len(body) < 16 or body[4:8] != b"ftyp":
        raise InvalidSpeech("container is not m4a")
    brands = _ftyp_brands(body)
    if not brands.intersection(ALLOWED_FTYP_BRANDS):
        raise InvalidSpeech("container is not m4a")
    duration_ms = _mvhd_duration_ms(body)
    if duration_ms < ASR_MIN_DURATION_MS:
        raise InvalidSpeech("duration is below 1 ms")
    if duration_ms > ASR_MAX_DURATION_MS:
        raise InvalidSpeech("duration exceeds 30 s")
    return AudioInspection(
        size_bytes=len(body),
        duration_ms=duration_ms,
        brands=tuple(sorted(brand.decode("ascii", errors="replace") for brand in brands)),
    )


def validate_asr_upload(*, mime_type: str | None, body: bytes) -> AudioInspection:
    normalize_audio_mime(mime_type)
    return inspect_m4a_bytes(body)


def audio_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def transcribe_request_hash(*, sha256: str, mime_type: str, size_bytes: int) -> str:
    canonical = json.dumps(
        {"mime_type": mime_type, "sha256": sha256, "size_bytes": size_bytes},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


@contextmanager
def temporary_asr_audio(body: bytes) -> Iterator[Path]:
    directory = Path(tempfile.gettempdir()) / "kelin-asr"
    directory.mkdir(mode=0o700, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=ASR_TEMP_PREFIX, suffix=".m4a", dir=directory)
    path = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        yield path
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            next(directory.iterdir())
        except StopIteration:
            try:
                directory.rmdir()
            except OSError:
                pass
        except OSError:
            pass


def require_transcript_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise EmptyTranscript("asr result is empty")
    return stripped


def require_voice_profile(value: str) -> str:
    if value != TTS_VOICE_DEFAULT:
        raise InvalidSpeech("voice_profile is not allowed")
    return value


def require_tts_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise InvalidSpeech("tts text is empty")
    if len(stripped) > TTS_MAX_CHARS:
        raise InvalidSpeech("tts text exceeds 200 characters")
    return stripped


def asr_quota_amount() -> int:
    return 1


def tts_quota_amount(*, cache_hit: bool) -> int:
    return 0 if cache_hit else 1


def tts_cache_key(*, message_id: UUID, voice_profile: str, model_alias: str) -> str:
    require_voice_profile(voice_profile)
    alias = model_alias.strip()
    if not alias:
        raise InvalidSpeech("model_alias is required")
    return f"{message_id}:{voice_profile}:{alias}"


def tts_object_path(*, owner_id: UUID, message_id: UUID, voice_profile: str) -> str:
    require_voice_profile(voice_profile)
    return f"{TTS_PATH_PREFIX}/{owner_id}/{message_id}/{voice_profile}.m4a"


def assert_tts_object_path(
    object_path: str, *, owner_id: UUID, message_id: UUID, voice_profile: str
) -> None:
    expected = tts_object_path(
        owner_id=owner_id, message_id=message_id, voice_profile=voice_profile
    )
    if object_path != expected:
        raise InvalidSpeech("object path must be server generated")
    parts = object_path.split("/")
    if len(parts) != 4 or parts[0] != TTS_PATH_PREFIX or not parts[3].endswith(".m4a"):
        raise InvalidSpeech("object path must have four segments")
    if parts[1] != str(owner_id):
        raise InvalidSpeech("object path owner mismatch")


def synthesize_request_hash(*, message_id: UUID, voice_profile: str) -> str:
    require_voice_profile(voice_profile)
    canonical = json.dumps(
        {"message_id": str(message_id), "voice_profile": voice_profile},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def playback_expires_at(now: datetime) -> datetime:
    return now + TTS_PLAYBACK_TTL


def cache_expires_at(created_at: datetime) -> datetime:
    return created_at + TTS_CACHE_TTL


def tts_cache_expired(*, created_at: datetime, now: datetime) -> bool:
    return now >= created_at + TTS_CACHE_TTL


def sign_playback_url(
    *,
    bucket: str,
    object_path: str,
    expires_at: datetime,
    secret: bytes,
) -> str:
    exp = int(expires_at.timestamp())
    material = f"{TTS_GET_METHOD}\n{bucket}\n{object_path}\n{exp}".encode()
    sig = hmac.new(secret, material, hashlib.sha256).hexdigest()
    encoded_path = quote(object_path, safe="/")
    return f"{_SIGNED_OBJECT_ORIGIN}/{bucket}/{encoded_path}?exp={exp}&sig={sig}"


def verify_signed_playback_url(url: str, *, secret: bytes, now: datetime) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "kelin.invalid":
        raise InvalidPlaybackUrl("playback url is not a kelin object url")
    prefix = "/object/"
    if not parsed.path.startswith(prefix):
        raise InvalidPlaybackUrl("playback url path is invalid")
    rest = parsed.path[len(prefix) :]
    bucket, sep, raw_path = rest.partition("/")
    if not sep or not bucket or not raw_path:
        raise InvalidPlaybackUrl("playback url path is invalid")
    object_path = unquote(raw_path)
    query = parse_qs(parsed.query)
    exp_values = query.get("exp", [])
    sig_values = query.get("sig", [])
    if len(exp_values) != 1 or len(sig_values) != 1:
        raise InvalidPlaybackUrl("playback url signature is invalid")
    try:
        exp = int(exp_values[0])
    except ValueError as exc:
        raise InvalidPlaybackUrl("playback url signature is invalid") from exc
    if now.timestamp() > exp:
        raise InvalidPlaybackUrl("playback url expired")
    material = f"{TTS_GET_METHOD}\n{bucket}\n{object_path}\n{exp}".encode()
    expected = hmac.new(secret, material, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig_values[0], expected):
        raise InvalidPlaybackUrl("playback url signature is invalid")
    if not object_path.startswith(f"{TTS_PATH_PREFIX}/"):
        raise InvalidPlaybackUrl("playback url path is invalid")
    return bucket, object_path


def tts_audio_duration_ms(body: bytes) -> int:
    if not body:
        raise InvalidSpeech("tts audio is empty")
    if len(body) < 16 or body[4:8] != b"ftyp":
        raise InvalidSpeech("tts audio is not m4a")
    duration_ms = _mvhd_duration_ms(body)
    if duration_ms < ASR_MIN_DURATION_MS:
        raise InvalidSpeech("tts duration is below 1 ms")
    return duration_ms


def m4a_fixture_bytes(*, duration_ms: int, timescale: int = 1000, pad: int = 16) -> bytes:
    if timescale <= 0 or pad < 0:
        raise InvalidSpeech("fixture timescale and pad must be positive")
    duration_units = int(round(duration_ms * timescale / 1000))
    mvhd = bytearray(100)
    mvhd[12:16] = timescale.to_bytes(4, "big")
    mvhd[16:20] = duration_units.to_bytes(4, "big")
    mvhd[20:24] = (0x00010000).to_bytes(4, "big")
    mvhd[24:26] = (0x0100).to_bytes(2, "big")
    mvhd[36:40] = (0x00010000).to_bytes(4, "big")
    mvhd[52:56] = (0x00010000).to_bytes(4, "big")
    mvhd[68:72] = (0x40000000).to_bytes(4, "big")
    mvhd[96:100] = (2).to_bytes(4, "big")
    moov = _box(b"moov", _box(b"mvhd", bytes(mvhd)))
    ftyp = _box(b"ftyp", b"M4A " + (0).to_bytes(4, "big") + b"M4A mp42isom")
    mdat = _box(b"mdat", b"\x00" * pad)
    return ftyp + moov + mdat


def _box(name: bytes, payload: bytes) -> bytes:
    if len(name) != 4:
        raise InvalidSpeech("box type must be four bytes")
    size = 8 + len(payload)
    return size.to_bytes(4, "big") + name + payload


def _ftyp_brands(body: bytes) -> set[bytes]:
    for name, start, end in _iter_boxes(body):
        if name != b"ftyp":
            continue
        if end - start < 8:
            raise InvalidSpeech("ftyp is truncated")
        brands = {body[start : start + 4]}
        offset = start + 8
        while offset + 4 <= end:
            brands.add(body[offset : offset + 4])
            offset += 4
        return brands
    raise InvalidSpeech("ftyp is missing")


def _mvhd_duration_ms(body: bytes) -> int:
    for name, start, end in _iter_boxes(body):
        if name != b"moov":
            continue
        for child, child_start, child_end in _iter_boxes(body, start, end):
            if child != b"mvhd":
                continue
            return _read_mvhd_duration_ms(body[child_start:child_end])
        raise InvalidSpeech("mvhd is missing")
    raise InvalidSpeech("moov is missing")


def _read_mvhd_duration_ms(payload: bytes) -> int:
    if len(payload) < 20:
        raise InvalidSpeech("mvhd is truncated")
    version = payload[0]
    if version == 1:
        if len(payload) < 32:
            raise InvalidSpeech("mvhd v1 is truncated")
        timescale = int.from_bytes(payload[20:24], "big")
        duration = int.from_bytes(payload[24:32], "big")
    elif version == 0:
        timescale = int.from_bytes(payload[12:16], "big")
        duration = int.from_bytes(payload[16:20], "big")
    else:
        raise InvalidSpeech("mvhd version is unsupported")
    if timescale <= 0:
        raise InvalidSpeech("timescale is invalid")
    return int(duration * 1000 // timescale)


def _iter_boxes(
    data: bytes, start: int = 0, end: int | None = None
) -> tuple[tuple[bytes, int, int], ...]:
    limit = len(data) if end is None else end
    offset = start
    found: list[tuple[bytes, int, int]] = []
    while offset + 8 <= limit:
        size = int.from_bytes(data[offset : offset + 4], "big")
        name = data[offset + 4 : offset + 8]
        if size == 0:
            found.append((name, offset + 8, limit))
            break
        header = 8
        if size == 1:
            if offset + 16 > limit:
                raise InvalidSpeech("box is truncated")
            size = int.from_bytes(data[offset + 8 : offset + 16], "big")
            header = 16
        if size < header or offset + size > limit:
            raise InvalidSpeech("box is truncated")
        found.append((name, offset + header, offset + size))
        offset += size
    return tuple(found)


assert ASR_DAILY_LIMIT == 60
assert TTS_DAILY_LIMIT == 20
