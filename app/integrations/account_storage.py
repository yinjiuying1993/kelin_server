"""Account deletion storage catalog. Spec §14.9.

Uses service-side prefix listing only for this worker. Client adapters stay list-denied.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.sight_upload import SIGHT_BUCKET, SIGHT_PATH_PREFIX
from app.domain.speech import TTS_BUCKET, TTS_PATH_PREFIX
from app.integrations.storage import PrivateSightStorage, PrivateTtsStorage


@dataclass(frozen=True, slots=True)
class AccountObjectRef:
    bucket: str
    object_path: str


class AccountStorageError(Exception):
    """Storage deletion failed and must be retried."""


class AccountObjectCatalog:
    def __init__(
        self,
        sight: PrivateSightStorage,
        tts: PrivateTtsStorage,
    ) -> None:
        self._sight = sight
        self._tts = tts

    def list_owner_page(
        self,
        owner_id: UUID,
        *,
        after: str | None,
        limit: int,
    ) -> list[AccountObjectRef]:
        prefix_sight = f"{SIGHT_PATH_PREFIX}/{owner_id}/"
        prefix_tts = f"{TTS_PATH_PREFIX}/{owner_id}/"
        listed = [
            AccountObjectRef(bucket=SIGHT_BUCKET, object_path=path)
            for path in self._sight.deletion_list_prefix(SIGHT_BUCKET, prefix_sight)
        ]
        listed.extend(
            AccountObjectRef(bucket=TTS_BUCKET, object_path=path)
            for path in self._tts.deletion_list_prefix(TTS_BUCKET, prefix_tts)
        )
        listed.sort(key=lambda item: f"{item.bucket}:{item.object_path}")
        if after is None:
            return listed[:limit]
        return [item for item in listed if f"{item.bucket}:{item.object_path}" > after][:limit]

    def delete_object(self, ref: AccountObjectRef) -> None:
        if ref.bucket == SIGHT_BUCKET:
            self._sight.service_delete(ref.bucket, ref.object_path)
            return
        if ref.bucket == TTS_BUCKET:
            self._tts.service_delete(ref.bucket, ref.object_path)
            return
        raise AccountStorageError("unknown bucket")
