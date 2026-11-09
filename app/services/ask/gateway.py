"""Ask HTTP calls. Model calls stay outside the database transactions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.db.session import claimed_read_transaction, claimed_transaction
from app.domain.match_index import MatchIndexError
from app.domain.match_index.rows import QuestionMaterial
from app.repositories.ask import SqlAskDialogue
from app.repositories.ask_follow import SqlAskFollow
from app.repositories.ask_history import SqlAskHistory
from app.repositories.ask_rating import SqlAskRating
from app.repositories.ask_turns import SqlAskTurns
from app.repositories.helped import SqlHelpedLedger
from app.repositories.match_confirm import SqlMatchConfirm
from app.repositories.match_index import SqlMatchIndexStore
from app.repositories.spirit_public import SqlPublicProfile
from app.schemas.ask import (
    AskQuestionDetail,
    AskQuestionPage,
    AskRateResult,
    AskRating,
    AskRecent,
    AskSubmitResult,
    AskTurnResult,
    AskView,
    HelpedResult,
    SpiritPublicProfile,
)
from app.services.ask.first_reply import FirstAnswer
from app.services.ask.follow import send_follow_up
from app.services.ask.history import (
    list_answered_questions,
    list_recent_questions,
    read_answered_question,
)
from app.services.ask.match import MatchChoice, match_published_skill
from app.services.ask.provider import ProviderAsk, ProviderMatch, ProviderOfficial
from app.services.ask.rate import submit_rating
from app.services.ask.submit import submit_question
from app.services.craft_ledger.helped import read_helped
from app.services.craft_ledger.public_profile import read_public_profile
from app.services.craft_self_test.provider import ProviderSelfTest
from app.services.match_index.index import MatchIndexService
from app.services.match_index.materials import SessionAskerSpirits, read_asker_materials


class _Provider(Protocol):
    async def moderate_text(self, text: str) -> str: ...

    async def complete_ask(self, instruction: str, user_text: str) -> str: ...

    async def complete_official(
        self, text: str, session_id: str | None
    ) -> tuple[str, str | None]: ...

    async def complete_match(self, instruction: str, user_text: str) -> list[str]: ...


class _Bound:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        user: CurrentUser,
    ) -> None:
        self._factory = factory
        self._user = user

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[AsyncSession]:
        async with claimed_transaction(self._factory, self._user) as session:
            yield session


class _Dialogues(_Bound):
    async def find(self, asker_user_id: UUID, client_id: UUID):  # type: ignore[no-untyped-def]
        async with self._tx() as session:
            return await SqlAskDialogue(session).find(asker_user_id, client_id)

    async def insert(self, **fields: object) -> UUID:
        async with self._tx() as session:
            return await SqlAskDialogue(session).insert(**fields)  # type: ignore[arg-type]

    async def snapshot(self, skill_slug: str, spirit_id: UUID):  # type: ignore[no-untyped-def]
        async with self._tx() as session:
            return await SqlAskDialogue(session).snapshot(skill_slug, spirit_id)

    async def official(self):  # type: ignore[no-untyped-def]
        async with self._tx() as session:
            return await SqlAskDialogue(session).official()


class _Turns(_Bound):
    async def find(self, question_id: UUID, asker_user_id: UUID, client_id: UUID):  # type: ignore[no-untyped-def]
        async with self._tx() as session:
            return await SqlAskTurns(session).find(question_id, asker_user_id, client_id)

    async def occupy(self, **fields: object):  # type: ignore[no-untyped-def]
        async with self._tx() as session:
            return await SqlAskTurns(session).occupy(**fields)  # type: ignore[arg-type]

    async def fail(self, **fields: object) -> None:
        async with self._tx() as session:
            await SqlAskTurns(session).fail(**fields)  # type: ignore[arg-type]

    async def succeed(self, **fields: object) -> datetime:
        async with self._tx() as session:
            return await SqlAskTurns(session).succeed(**fields)  # type: ignore[arg-type]

    async def mark_history(
        self, question_id: UUID, asker_user_id: UUID, answered_at: datetime
    ) -> datetime:
        async with self._tx() as session:
            return await SqlAskTurns(session).mark_history(
                question_id, asker_user_id, answered_at
            )

    async def save_session(self, question_id: UUID, asker_user_id: UUID, session_id: str) -> None:
        async with self._tx() as session:
            await SqlAskTurns(session).save_session(question_id, asker_user_id, session_id)


class _Loaded:
    def __init__(self, rows: list[QuestionMaterial]) -> None:
        self._rows = rows

    async def load(self) -> list[QuestionMaterial]:
        return self._rows


class _Confirm(_Bound):
    async def confirm(self, skill_slugs: tuple[str, ...]):  # type: ignore[no-untyped-def]
        async with self._tx() as session:
            return await SqlMatchConfirm(session).confirm(skill_slugs)


class _Matcher(_Bound):
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        user: CurrentUser,
        provider: _Provider,
    ) -> None:
        super().__init__(factory, user)
        self._provider = provider

    async def match(self, question: str) -> MatchChoice | None:
        async with self._tx() as session:
            spirits = SessionAskerSpirits(session)
            spirit_id = await spirits.spirit_id(self._user.id)
            if spirit_id is None:
                raise ApiError("NOT_FOUND", public_error_message("NOT_FOUND"), status_code=404)
            try:
                loaded = await read_asker_materials(
                    MatchIndexService(SqlMatchIndexStore(session)),
                    spirits,
                    asker_user_id=self._user.id,
                    asker_spirit_id=spirit_id,
                )
            except MatchIndexError as exc:
                raise _unavailable() from exc
        return await match_published_skill(
            _Loaded(loaded),
            _Confirm(self._factory, self._user),
            ProviderMatch(self._provider),
            asker_spirit_id=spirit_id,
            question=question,
        )


async def submit_http(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    provider: _Provider,
    *,
    client_id: UUID,
    text: str,
    now: datetime,
) -> AskSubmitResult:
    result = await submit_question(
        _Dialogues(factory, user),
        _Matcher(factory, user, provider),
        _Turns(factory, user),
        ProviderAsk(provider),
        ProviderSelfTest(provider),
        asker_user_id=user.id,
        client_id=client_id,
        question=text,
        now=now,
        official_model=ProviderOfficial(provider),
    )
    if isinstance(result, FirstAnswer):
        return AskSubmitResult(
            matched=True,
            question=await _detail(factory, user, result.question_id),
        )
    return result


async def follow_http(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    provider: _Provider,
    *,
    question_id: UUID,
    client_id: UUID,
    text: str,
    now: datetime,
) -> AskTurnResult:
    reply = await send_follow_up(
        _Follow(factory, user),
        _Turns(factory, user),
        ProviderAsk(provider),
        ProviderSelfTest(provider),
        asker_user_id=user.id,
        question_id=question_id,
        client_id=client_id,
        text=text,
        now=now,
        official_model=ProviderOfficial(provider),
    )
    return AskTurnResult(question_id=reply.question_id, reply=reply.reply_text)


class _Follow(_Bound):
    async def load(self, asker_user_id: UUID, question_id: UUID):  # type: ignore[no-untyped-def]
        async with self._tx() as session:
            return await SqlAskFollow(session).load(asker_user_id, question_id)


async def rate_http(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    *,
    question_id: UUID,
    client_id: UUID,
    rating: AskRating,
) -> AskRateResult:
    async with claimed_transaction(factory, user) as session:
        return await submit_rating(
            SqlAskRating(session),
            asker_user_id=user.id,
            question_id=question_id,
            client_id=client_id,
            rating=rating,
        )


async def list_http(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    *,
    view: AskView,
    cursor: str | None,
    secret: bytes,
    now: datetime,
) -> AskQuestionPage:
    async with claimed_read_transaction(factory, user) as session:
        return await list_answered_questions(
            SqlAskHistory(session),
            asker_user_id=user.id,
            view=view,
            cursor=cursor,
            secret=secret,
            now=now,
        )


async def recent_http(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    *,
    now: datetime,
) -> AskRecent:
    async with claimed_read_transaction(factory, user) as session:
        return await list_recent_questions(
            SqlAskHistory(session),
            asker_user_id=user.id,
            now=now,
        )


async def detail_http(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    *,
    question_id: UUID,
) -> AskQuestionDetail:
    return await _detail(factory, user, question_id)


async def helped_http(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
) -> HelpedResult:
    async with claimed_read_transaction(factory, user) as session:
        return await read_helped(SqlHelpedLedger(session), owner_id=user.id)


async def profile_http(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    *,
    spirit_id: UUID,
) -> SpiritPublicProfile:
    async with claimed_read_transaction(factory, user) as session:
        return await read_public_profile(SqlPublicProfile(session), spirit_id=spirit_id)


async def _detail(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    question_id: UUID,
) -> AskQuestionDetail:
    async with claimed_read_transaction(factory, user) as session:
        return await read_answered_question(
            SqlAskHistory(session),
            asker_user_id=user.id,
            question_id=question_id,
        )


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )
