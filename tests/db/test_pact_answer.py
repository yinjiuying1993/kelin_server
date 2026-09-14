from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.providers.pact_feedback import FailingPactFeedbackProvider
from app.schemas.pact import CreatePactRequest, PactAnswerRequest, PactSessionRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.pact import create_pact, open_pact_session, submit_pact_answer
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)


def _spirit_request() -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "egg": "warm",
            "name": "未名",
            "consents": {
                "ai_disclosure": {
                    "document_version": "2026-09",
                    "explicitly_accepted": True,
                },
                "data_notice": {"document_version": "2026-09", "displayed": True},
                "user_terms": {"document_version": "2026-09", "displayed": True},
            },
        }
    )


def _pact() -> CreatePactRequest:
    return CreatePactRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "theme": "interview",
            "title": "面试准备",
        }
    )


def _session(pact_id: uuid.UUID) -> PactSessionRequest:
    return PactSessionRequest.model_validate(
        {"client_id": str(uuid.uuid4()), "pact_id": str(pact_id)}
    )


def _answer(
    session_id: uuid.UUID,
    question_id: str,
    *,
    client_id: uuid.UUID | None = None,
    expected: int = 1,
    text: str = "我想加入因为方向匹配。",
) -> PactAnswerRequest:
    return PactAnswerRequest.model_validate(
        {
            "client_id": str(client_id or uuid.uuid4()),
            "session_id": str(session_id),
            "expected_session_version": expected,
            "question_id": question_id,
            "text": text,
        }
    )


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _boot(url: str, factory: Any, user_id: uuid.UUID) -> CurrentUser:
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        await create_spirit_if_absent(session, user, _spirit_request())
    return user


def test_pact_answer_incremental_replay_conflict_order_and_provider() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_answer(url))


async def _assert_answer(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = await _boot(url, factory, uuid.uuid4())
    other = await _boot(url, factory, uuid.uuid4())

    async with claimed_transaction(factory, owner) as session:
        pact = await create_pact(session, owner, _pact(), now=NOW)
    async with claimed_transaction(factory, owner) as session:
        opened = await open_pact_session(session, owner, _session(pact.pact.id), now=NOW)
    session_id = opened.session.id
    q1, q2, q3 = [item.question_id for item in opened.session.questions]
    assert opened.session.row_version == 1
    first_client = uuid.uuid4()

    async with claimed_transaction(factory, owner) as session:
        first = await submit_pact_answer(
            session, owner, _answer(session_id, q1, client_id=first_client), now=NOW
        )
    assert first.resource.finalized is False
    assert first.resource.answered_count == 1
    assert first.resource.score is None
    assert first.resource.pact_status is None
    assert first.resource.row_version == 2
    assert first.pact.completed_sessions == 0
    assert first.resource.feedback.summary == "方向清楚，可以再具体一点。"

    async with claimed_transaction(factory, owner) as session:
        replay = await submit_pact_answer(
            session, owner, _answer(session_id, q1, client_id=first_client), now=NOW
        )
    assert replay.resource.question_id == q1
    assert replay.resource.answered_count == 1
    assert replay.resource.row_version == 2
    assert replay.resource.feedback.summary == first.resource.feedback.summary

    async with claimed_transaction(factory, owner) as session:
        try:
            await submit_pact_answer(
                session,
                owner,
                _answer(session_id, q1, client_id=first_client, text="另一段回答"),
                now=NOW,
            )
        except ApiError as exc:
            assert exc.code == "PACT_QUESTION_ALREADY_ANSWERED"
        else:
            raise AssertionError("changed text must not overwrite")

    async with claimed_transaction(factory, owner) as session:
        try:
            await submit_pact_answer(session, owner, _answer(session_id, q1), now=NOW)
        except ApiError as exc:
            assert exc.code == "PACT_QUESTION_ALREADY_ANSWERED"
        else:
            raise AssertionError("different client_id must not overwrite")

    async with claimed_transaction(factory, owner) as session:
        try:
            await submit_pact_answer(session, owner, _answer(session_id, q2, expected=1), now=NOW)
        except ApiError as exc:
            assert exc.code == "CONFLICT"
        else:
            raise AssertionError("stale session version must conflict")

    async with claimed_transaction(factory, owner) as session:
        second = await submit_pact_answer(
            session, owner, _answer(session_id, q2, expected=2), now=NOW
        )
    assert second.resource.answered_count == 2
    assert second.resource.finalized is False
    assert second.resource.row_version == 3
    assert second.pact.completed_sessions == 0

    async with claimed_transaction(factory, owner) as session:
        try:
            await submit_pact_answer(session, owner, _answer(session_id, q3, expected=1), now=NOW)
        except ApiError as exc:
            assert exc.code == "CONFLICT"
        else:
            raise AssertionError("q3 with stale version must conflict")

    async with claimed_transaction(factory, owner) as session:
        third = await submit_pact_answer(
            session, owner, _answer(session_id, q3, expected=3), now=NOW
        )
    assert third.resource.answered_count == 3
    assert third.resource.finalized is True
    assert third.resource.score is not None
    assert third.resource.pact_status == "active"
    assert third.pact.completed_sessions == 1
    assert third.pact.status == "active"
    assert third.resource.feedback.summary == "三题已齐，本场结束。"
    assert third.resource.row_version == 4

    async with claimed_transaction(factory, owner) as session:
        try:
            await submit_pact_answer(
                session,
                owner,
                _answer(session_id, "interview-v1-q99", expected=3),
                now=NOW,
            )
        except ApiError as exc:
            assert exc.code == "PACT_QUESTION_NOT_FOUND"
        else:
            raise AssertionError("unknown question must be hidden")

    skip_user = await _boot(url, factory, uuid.uuid4())
    async with claimed_transaction(factory, skip_user) as session:
        skip_pact = await create_pact(session, skip_user, _pact(), now=NOW)
    async with claimed_transaction(factory, skip_user) as session:
        skip_opened = await open_pact_session(
            session, skip_user, _session(skip_pact.pact.id), now=NOW
        )
    skip_q2 = skip_opened.session.questions[1].question_id
    async with claimed_transaction(factory, skip_user) as session:
        try:
            await submit_pact_answer(
                session, skip_user, _answer(skip_opened.session.id, skip_q2), now=NOW
            )
        except ApiError as exc:
            assert exc.code == "INVALID_INPUT"
        else:
            raise AssertionError("out-of-order answer must be rejected")

    async with claimed_transaction(factory, other) as session:
        try:
            await submit_pact_answer(session, other, _answer(session_id, q1, expected=2), now=NOW)
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("foreign session must be hidden")

    fail_user = await _boot(url, factory, uuid.uuid4())
    async with claimed_transaction(factory, fail_user) as session:
        fail_pact = await create_pact(session, fail_user, _pact(), now=NOW)
    async with claimed_transaction(factory, fail_user) as session:
        fail_opened = await open_pact_session(
            session, fail_user, _session(fail_pact.pact.id), now=NOW
        )
    fail_q1 = fail_opened.session.questions[0].question_id
    async with claimed_transaction(factory, fail_user) as session:
        fallback = await submit_pact_answer(
            session,
            fail_user,
            _answer(fail_opened.session.id, fail_q1),
            now=NOW,
            feedback_provider=FailingPactFeedbackProvider(),
        )
    assert fallback.resource.finalized is False
    assert fallback.resource.answered_count == 1
    assert fallback.resource.feedback.summary == "先把这件事讲完整，再补一句结果。"
    assert fallback.pact.completed_sessions == 0

    await engine.dispose()
