"""Friends, postcards, and visit public contract. Spec §§8.9, 14.1–14.5, 15.1.

Visit planning/settle is worker-only. OpenAPI locks the result model and whitelist
so clients cannot send host/NPC, score, or private fields. There is no public
visit create route.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.domain.invite_code import invite_code_is_valid, normalize_invite_code
from app.schemas.envelope import EnvelopeError
from app.schemas.spirit import MutationEvent, QuotaUsage, SpiritStage, SpiritStatus

MAX_VISIT_DESTINATIONS = 2
NPC_IDS = ("fog", "lamp", "silent")
PUBLIC_PROFILE_FIELD_NAMES = frozenset({"id", "title", "stage", "public_marks", "status"})
PUBLIC_NPC_FIELD_NAMES = frozenset({"npc_id", "title", "public_marks"})
PUBLIC_VISIT_CONTEXT_FIELD_NAMES = frozenset({"title", "stage", "weather", "public_marks"})
PRIVATE_SOCIAL_FIELD_NAMES = frozenset(
    {
        "user_id",
        "email",
        "avatar",
        "photo",
        "image",
        "latitude",
        "longitude",
        "location",
        "memory",
        "memories",
        "conversation",
        "content",
        "prompt",
        "hunger",
        "energy",
        "mood",
        "bond",
        "closeness",
        "curiosity",
        "sharpness",
        "nocturnal",
        "stubborn",
    }
)
SOCIAL_EVENT_TYPES = frozenset({"friend.added", "friend.removed", "postcard.read", "visit.settled"})


def _normalized_invite_code(value: str) -> str:
    code = normalize_invite_code(value)
    if not invite_code_is_valid(code):
        raise ValueError("invite_code must be 8 characters without 0/O/1/I")
    return code


InviteCode = Annotated[str, AfterValidator(_normalized_invite_code)]


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicSpiritProfile(_ForbidExtra):
    id: UUID
    title: str = Field(min_length=1, max_length=40)
    stage: SpiritStage
    public_marks: list[str] = Field(default_factory=list)
    status: SpiritStatus


class NpcPublicProfile(_ForbidExtra):
    npc_id: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=40)
    public_marks: list[str] = Field(default_factory=list)


class PublicVisitContext(_ForbidExtra):
    title: str = Field(min_length=1, max_length=40)
    stage: SpiritStage
    weather: str = Field(min_length=1, max_length=32)
    public_marks: list[str] = Field(default_factory=list)


class VisitPublic(_ForbidExtra):
    type: Literal["visit"] = "visit"
    id: UUID
    plan_id: UUID
    destination_index: Literal[1, 2]
    status: Literal["eligible", "visiting", "settled", "cancelled", "failed"]
    host: PublicSpiritProfile | None = None
    npc: NpcPublicProfile | None = None
    public_context: PublicVisitContext

    @model_validator(mode="after")
    def host_xor_npc(self) -> Self:
        if (self.host is None) == (self.npc is None):
            raise ValueError("visit must have exactly one of host or npc")
        return self


class VisitPlanPublic(_ForbidExtra):
    plan_id: UUID
    visits: list[VisitPublic] = Field(default_factory=list, max_length=MAX_VISIT_DESTINATIONS)

    @model_validator(mode="after")
    def destinations_unique_and_bounded(self) -> Self:
        if len(self.visits) > MAX_VISIT_DESTINATIONS:
            raise ValueError("a visit plan has at most two destinations")
        indexes = [item.destination_index for item in self.visits]
        if len(set(indexes)) != len(indexes):
            raise ValueError("destination_index must be unique in a plan")
        return self


class FriendPublic(_ForbidExtra):
    friend_id: UUID
    spirit: PublicSpiritProfile
    created_at: str


class FriendPage(_ForbidExtra):
    items: list[FriendPublic]
    next_cursor: str | None = Field(
        default=None,
        description="Opaque keyset cursor. Null on the last page. Do not treat as SQL.",
    )
    has_more: bool
    snapshot_at: str

    @model_validator(mode="after")
    def cursor_matches_has_more(self) -> Self:
        if self.has_more and self.next_cursor is None:
            raise ValueError("next_cursor is required when has_more is true")
        if not self.has_more and self.next_cursor is not None:
            raise ValueError("next_cursor must be null on the last page")
        return self


class FriendListQuery(_ForbidExtra):
    cursor: str | None = Field(default=None, min_length=1)
    limit: int = Field(default=30, ge=1, le=50)


class AddFriendRequest(_ForbidExtra):
    client_id: UUID
    invite_code: InviteCode


class RemoveFriendRequest(_ForbidExtra):
    client_id: UUID


class SocialPatch(_ForbidExtra):
    friends_upsert: list[FriendPublic] = Field(default_factory=list)
    friends_removed: list[UUID] = Field(default_factory=list)
    visit_plan: VisitPlanPublic | None = None


class SocialMutationPatch(_ForbidExtra):
    snapshot_version: int = Field(ge=1)
    spirit: None = None
    preferences: None = None
    room: None = None
    onboarding: None = None
    report: None = None
    social: SocialPatch | None = None
    memories_upsert: list[object] = Field(default_factory=list)
    memory_tombstones: list[object] = Field(default_factory=list)
    postcards_upsert: list[PostcardPublic] = Field(default_factory=list)
    pact: None = None


class AddFriendResult(_ForbidExtra):
    resource: FriendPublic
    patch: SocialMutationPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def patch_includes_friend(self) -> Self:
        if self.patch.social is None:
            raise ValueError("add friend must patch social")
        upsert_ids = {item.friend_id for item in self.patch.social.friends_upsert}
        if self.resource.friend_id not in upsert_ids:
            raise ValueError("add friend must upsert the created edge")
        return self


class RemoveFriendResource(_ForbidExtra):
    type: Literal["friend"] = "friend"
    id: UUID


class RemoveFriendResult(_ForbidExtra):
    resource: RemoveFriendResource
    patch: SocialMutationPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def patch_removes_friend(self) -> Self:
        if self.patch.social is None:
            raise ValueError("remove friend must patch social")
        if self.resource.id not in self.patch.social.friends_removed:
            raise ValueError("remove friend must list the removed friend_id")
        return self


class PostcardPublic(_ForbidExtra):
    id: UUID
    visit_id: UUID
    sender: PublicSpiritProfile | None = None
    npc: NpcPublicProfile | None = None
    text: str = Field(min_length=1, max_length=300)
    read_at: str | None = None
    created_at: str
    visit: VisitPublic

    @model_validator(mode="after")
    def sender_xor_npc(self) -> Self:
        if (self.sender is None) == (self.npc is None):
            raise ValueError("postcard must have exactly one of sender or npc")
        if self.visit.id != self.visit_id:
            raise ValueError("postcard.visit.id must match visit_id")
        return self


class PostcardPage(_ForbidExtra):
    items: list[PostcardPublic]
    next_cursor: str | None = Field(
        default=None,
        description="Opaque keyset cursor. Null on the last page. Do not treat as SQL.",
    )
    has_more: bool
    snapshot_at: str

    @model_validator(mode="after")
    def cursor_matches_has_more(self) -> Self:
        if self.has_more and self.next_cursor is None:
            raise ValueError("next_cursor is required when has_more is true")
        if not self.has_more and self.next_cursor is not None:
            raise ValueError("next_cursor must be null on the last page")
        return self


class PostcardListQuery(_ForbidExtra):
    unread_only: bool = False
    cursor: str | None = Field(default=None, min_length=1)
    limit: int = Field(default=30, ge=1, le=50)


class PostcardReadRequest(_ForbidExtra):
    client_id: UUID


class PostcardReadResource(_ForbidExtra):
    type: Literal["postcard"] = "postcard"
    id: UUID
    read_at: str


class PostcardReadResult(_ForbidExtra):
    resource: PostcardReadResource
    patch: SocialMutationPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def patch_upserts_postcard(self) -> Self:
        upserted = [item for item in self.patch.postcards_upsert if item.id == self.resource.id]
        if len(upserted) != 1:
            raise ValueError("postcard read must upsert the same postcard")
        if upserted[0].read_at != self.resource.read_at:
            raise ValueError("read_at must match the upserted postcard")
        return self


class FriendPageSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: FriendPage
    error: None = None
    request_id: str
    server_time: str


class PostcardPageSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: PostcardPage
    error: None = None
    request_id: str
    server_time: str


class AddFriendSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: AddFriendResult
    error: None = None
    request_id: str
    server_time: str


class RemoveFriendSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: RemoveFriendResult
    error: None = None
    request_id: str
    server_time: str


class PostcardReadSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: PostcardReadResult
    error: None = None
    request_id: str
    server_time: str


class SocialErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
