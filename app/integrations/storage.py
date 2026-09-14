"""Private object adapters. Spec §§11.3–11.4, 10.5.

Clients may only PUT a single signed sight object. TTS writes are service-side.
Adapters get/delete by an already owner-checked key and have no list grant.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

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


class StoragePutRejected(Exception):
    """Signed PUT failed MIME magic, size, or signature checks."""


class PrivateSightStorage:
    """In-process private bucket used when no live Storage project is injected."""

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}

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
        self._objects[(bucket, object_path)] = bytes(body)

    def client_get(self, bucket: str, object_path: str) -> bytes:
        raise StorageAccessDenied("client has no read policy")

    def client_delete(self, bucket: str, object_path: str) -> None:
        raise StorageAccessDenied("client has no delete policy")

    def client_list(self, bucket: str, prefix: str) -> list[str]:
        raise StorageListDenied("client has no list policy")

    def service_get(self, bucket: str, object_path: str) -> bytes:
        try:
            return self._objects[(bucket, object_path)]
        except KeyError as exc:
            raise StorageObjectMissing("object is missing") from exc

    def service_delete(self, bucket: str, object_path: str) -> bool:
        return self._objects.pop((bucket, object_path), None) is not None

    def service_list(self, bucket: str, prefix: str) -> list[str]:
        raise StorageListDenied("cleanup adapter has no bucket list grant")

    def deletion_list_prefix(self, bucket: str, prefix: str) -> list[str]:
        """Account-deletion worker listing. Not a client/cleanup list grant."""
        return sorted(
            path
            for key_bucket, path in self._objects
            if key_bucket == bucket and path.startswith(prefix)
        )

    def seed_object(self, bucket: str, object_path: str, body: bytes) -> None:
        """Test helper to place bytes without a signed PUT."""
        self._objects[(bucket, object_path)] = bytes(body)


class PrivateTtsStorage:
    """In-process private TTS bucket. Cache index is not a bucket list grant."""

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}
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
        self._objects[(bucket, object_path)] = bytes(body)

    def service_get(self, bucket: str, object_path: str) -> bytes:
        try:
            return self._objects[(bucket, object_path)]
        except KeyError as exc:
            raise StorageObjectMissing("object is missing") from exc

    def service_delete(self, bucket: str, object_path: str) -> bool:
        return self._objects.pop((bucket, object_path), None) is not None

    def service_list(self, bucket: str, prefix: str) -> list[str]:
        del bucket, prefix
        raise StorageListDenied("tts cache must not list the bucket")

    def deletion_list_prefix(self, bucket: str, prefix: str) -> list[str]:
        """Account-deletion worker listing. Not a client/cleanup list grant."""
        return sorted(
            path
            for key_bucket, path in self._objects
            if key_bucket == bucket and path.startswith(prefix)
        )

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
        if (TTS_BUCKET, record.object_path) not in self._objects:
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
