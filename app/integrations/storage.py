"""Private object adapters. Spec §§11.3–11.4, 10.5.

Clients may only PUT a single signed sight object. TTS writes are service-side.
Adapters get/delete by an already owner-checked key and have no list grant.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.visit_card import (
    CARD_MEDIA_MAX_BYTES,
    VISIT_CARD_BUCKET,
    VISIT_CARD_KEY_PREFIX,
    visit_card_magic_ok,
    visit_card_media_type,
)
from app.domain.sight_upload import (
    ALLOWED_MIME,
    JPEG_MAGIC,
    MAX_SIZE_BYTES,
    MIN_SIZE_BYTES,
    InvalidSightUpload,
    verify_signed_put_url,
)
from app.domain.speech import (
    TTS_BUCKET,
    TTS_OUTPUT_MIME,
    TtsCacheRecord,
    tts_cache_expired,
    tts_cache_key,
)


class StorageAccessDenied(Exception):
    """Client attempted list/read/delete or unsigned write."""


class StorageListDenied(Exception):
    """Bucket list is not granted to the upload cleanup adapter."""


class StorageObjectMissing(Exception):
    """Exact object key is not present."""


class StorageUnavailable(Exception):
    """Object storage could not be reached. Callers must not treat this as a missing file."""


class ObjectBackend(Protocol):
    def put(self, bucket: str, object_path: str, body: bytes, *, content_type: str) -> None: ...

    def get(self, bucket: str, object_path: str) -> bytes: ...

    def delete(self, bucket: str, object_path: str) -> bool: ...

    def list_prefix(self, bucket: str, prefix: str) -> list[str]: ...

    def contains(self, bucket: str, object_path: str) -> bool: ...


class _MemoryObjects:
    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}

    def put(self, bucket: str, object_path: str, body: bytes, *, content_type: str) -> None:
        del content_type
        self._objects[(bucket, object_path)] = bytes(body)

    def get(self, bucket: str, object_path: str) -> bytes:
        try:
            return self._objects[(bucket, object_path)]
        except KeyError as exc:
            raise StorageObjectMissing("object is missing") from exc

    def delete(self, bucket: str, object_path: str) -> bool:
        return self._objects.pop((bucket, object_path), None) is not None

    def list_prefix(self, bucket: str, prefix: str) -> list[str]:
        return sorted(
            path
            for key_bucket, path in self._objects
            if key_bucket == bucket and path.startswith(prefix)
        )

    def contains(self, bucket: str, object_path: str) -> bool:
        return (bucket, object_path) in self._objects


class StoragePutRejected(Exception):
    """Signed PUT failed MIME magic, size, or signature checks."""

    def __init__(self, message: str, *, public_code: str = "NOT_FOUND") -> None:
        super().__init__(message)
        self.public_code = public_code


class PrivateSightStorage:
    """Private sight bucket. Bytes stay in memory unless an object backend is injected."""

    def __init__(self, backend: ObjectBackend | None = None) -> None:
        self._blobs: ObjectBackend = backend if backend is not None else _MemoryObjects()

    def client_put(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
        *,
        secret: bytes,
        now: datetime,
    ) -> None:
        try:
            bucket, object_path = verify_signed_put_url(url, secret=secret, now=now)
        except InvalidSightUpload as exc:
            raise StoragePutRejected(str(exc)) from exc
        content_type = headers.get("content-type")
        if content_type != ALLOWED_MIME:
            raise StoragePutRejected("content-type must be image/jpeg")
        size = len(body)
        if size < MIN_SIZE_BYTES or size > MAX_SIZE_BYTES:
            raise StoragePutRejected("size is outside 1..5 MiB")
        if not body.startswith(JPEG_MAGIC):
            raise StoragePutRejected("jpeg magic mismatch")
        self._blobs.put(bucket, object_path, body, content_type=ALLOWED_MIME)

    def client_get(self, bucket: str, object_path: str) -> bytes:
        raise StorageAccessDenied("client has no read policy")

    def client_delete(self, bucket: str, object_path: str) -> None:
        raise StorageAccessDenied("client has no delete policy")

    def client_list(self, bucket: str, prefix: str) -> list[str]:
        raise StorageListDenied("client has no list policy")

    def service_get(self, bucket: str, object_path: str) -> bytes:
        return self._blobs.get(bucket, object_path)

    def service_delete(self, bucket: str, object_path: str) -> bool:
        return self._blobs.delete(bucket, object_path)

    def service_list(self, bucket: str, prefix: str) -> list[str]:
        raise StorageListDenied("cleanup adapter has no bucket list grant")

    def deletion_list_prefix(self, bucket: str, prefix: str) -> list[str]:
        """Account-deletion worker listing. Not a client/cleanup list grant."""
        return self._blobs.list_prefix(bucket, prefix)

    def seed_object(self, bucket: str, object_path: str, body: bytes) -> None:
        """Test helper to place bytes without a signed PUT."""
        self._blobs.put(bucket, object_path, body, content_type=ALLOWED_MIME)


class PrivateTtsStorage:
    """Private TTS bucket. Cache index stays in process; bytes follow the object backend."""

    def __init__(self, backend: ObjectBackend | None = None) -> None:
        self._blobs: ObjectBackend = backend if backend is not None else _MemoryObjects()
        self._cache: dict[tuple[UUID, str], TtsCacheRecord] = {}

    def client_get(self, bucket: str, object_path: str) -> bytes:
        del bucket, object_path
        raise StorageAccessDenied("client has no unsigned read policy")

    def client_delete(self, bucket: str, object_path: str) -> None:
        del bucket, object_path
        raise StorageAccessDenied("client has no delete policy")

    def client_list(self, bucket: str, prefix: str) -> list[str]:
        del bucket, prefix
        raise StorageListDenied("client has no list policy")

    def service_put(self, bucket: str, object_path: str, body: bytes) -> None:
        self._blobs.put(bucket, object_path, body, content_type=TTS_OUTPUT_MIME)

    def service_get(self, bucket: str, object_path: str) -> bytes:
        return self._blobs.get(bucket, object_path)

    def service_delete(self, bucket: str, object_path: str) -> bool:
        return self._blobs.delete(bucket, object_path)

    def service_list(self, bucket: str, prefix: str) -> list[str]:
        del bucket, prefix
        raise StorageListDenied("tts cache must not list the bucket")

    def deletion_list_prefix(self, bucket: str, prefix: str) -> list[str]:
        """Account-deletion worker listing. Not a client/cleanup list grant."""
        return self._blobs.list_prefix(bucket, prefix)

    def put_cache(self, record: TtsCacheRecord) -> None:
        self._cache[
            (
                record.owner_id,
                tts_cache_key(
                    message_id=record.message_id,
                    voice_profile=record.voice_profile,
                    model_alias=record.model_alias,
                ),
            )
        ] = record

    def get_cache(self, owner_id: UUID, cache_key: str, *, now: datetime) -> TtsCacheRecord | None:
        record = self._cache.get((owner_id, cache_key))
        if record is None:
            return None
        if tts_cache_expired(created_at=record.created_at, now=now):
            self.expire_record(record)
            return None
        if not self._blobs.contains(TTS_BUCKET, record.object_path):
            self._cache.pop((owner_id, cache_key), None)
            return None
        return record

    def expire_due(self, *, now: datetime) -> int:
        due = [
            record
            for record in tuple(self._cache.values())
            if tts_cache_expired(created_at=record.created_at, now=now)
        ]
        for record in due:
            self.expire_record(record)
        return len(due)

    def expire_record(self, record: TtsCacheRecord) -> None:
        self.service_delete(TTS_BUCKET, record.object_path)
        self._cache.pop(
            (
                record.owner_id,
                tts_cache_key(
                    message_id=record.message_id,
                    voice_profile=record.voice_profile,
                    model_alias=record.model_alias,
                ),
            ),
            None,
        )


def _content_type(headers: dict[str, str]) -> str:
    raw = headers.get("content-type") or headers.get("Content-Type") or ""
    return raw.split(";", 1)[0].strip().lower()


class PrivateVisitCardStorage:
    """Visit-card voice and images. Signature is the write grant; bytes follow the backend."""

    def __init__(self, backend: ObjectBackend | None = None) -> None:
        self._blobs: ObjectBackend = backend if backend is not None else _MemoryObjects()

    def client_put(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
        *,
        secret: bytes,
        now: datetime,
    ) -> None:
        try:
            bucket, object_path = verify_signed_put_url(url, secret=secret, now=now)
        except InvalidSightUpload as exc:
            raise StoragePutRejected(str(exc)) from exc
        if bucket != VISIT_CARD_BUCKET or not _visit_card_path(object_path):
            raise StoragePutRejected("object path is not a visit card")
        mime = visit_card_media_type(object_path)
        if mime is None or _content_type(headers) != mime:
            code = "CARD_VOICE_REJECTED" if mime and mime.startswith("audio/") else "CARD_IMAGE_REJECTED"
            if mime is None and object_path.lower().endswith((".m4a", ".mp3")):
                code = "CARD_VOICE_REJECTED"
            raise StoragePutRejected("content-type does not match the object", public_code=code)
        if len(body) < MIN_SIZE_BYTES or len(body) > CARD_MEDIA_MAX_BYTES:
            code = "CARD_VOICE_REJECTED" if mime.startswith("audio/") else "CARD_IMAGE_REJECTED"
            raise StoragePutRejected("size is outside 1..5 MiB", public_code=code)
        if not visit_card_magic_ok(mime, body):
            code = "CARD_VOICE_REJECTED" if mime.startswith("audio/") else "CARD_IMAGE_REJECTED"
            raise StoragePutRejected("media magic mismatch", public_code=code)
        self._blobs.put(bucket, object_path, body, content_type=mime)

    def service_get(self, bucket: str, object_path: str) -> bytes:
        return self._blobs.get(bucket, object_path)

    def service_delete(self, bucket: str, object_path: str) -> bool:
        return self._blobs.delete(bucket, object_path)

    def deletion_list_prefix(self, bucket: str, prefix: str) -> list[str]:
        return self._blobs.list_prefix(bucket, prefix)


def _visit_card_path(object_path: str) -> bool:
    if not object_path.startswith(VISIT_CARD_KEY_PREFIX):
        return False
    return ".." not in object_path.split("/")
