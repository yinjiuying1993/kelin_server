"""Map friend mutation settlement to public envelope data. HTTP DTO only."""

from __future__ import annotations

from app.schemas.social_api import (
    AddFriendResult,
    PostcardReadResource,
    PostcardReadResult,
    RemoveFriendResource,
    RemoveFriendResult,
    SocialMutationPatch,
    SocialPatch,
)
from app.services.friends import (
    FriendAddSettlement,
    FriendRemoveSettlement,
    public_friend_from_row,
)
from app.services.postcards import PostcardReadSettlement


def add_friend_from_settlement(settlement: FriendAddSettlement) -> AddFriendResult:
    resource = public_friend_from_row(settlement.edge)
    return AddFriendResult(
        resource=resource,
        patch=SocialMutationPatch(
            snapshot_version=settlement.snapshot_version,
            social=SocialPatch(friends_upsert=[resource]),
        ),
        events=list(settlement.events),
    )


def remove_friend_from_settlement(settlement: FriendRemoveSettlement) -> RemoveFriendResult:
    return RemoveFriendResult(
        resource=RemoveFriendResource(id=settlement.friend_id),
        patch=SocialMutationPatch(
            snapshot_version=settlement.snapshot_version,
            social=SocialPatch(friends_removed=[settlement.friend_id]),
        ),
        events=list(settlement.events),
    )


def read_postcard_from_settlement(settlement: PostcardReadSettlement) -> PostcardReadResult:
    if settlement.postcard.read_at is None:
        raise RuntimeError("read settlement missing read_at")
    return PostcardReadResult(
        resource=PostcardReadResource(
            id=settlement.postcard.id,
            read_at=settlement.postcard.read_at,
        ),
        patch=SocialMutationPatch(
            snapshot_version=settlement.snapshot_version,
            postcards_upsert=[settlement.postcard],
        ),
        events=list(settlement.events),
    )
