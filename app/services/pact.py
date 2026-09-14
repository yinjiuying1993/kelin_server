"""Create one active pact, open the daily session, save answers, skip, and finalize.

Skip does not count a completed session. The last missing answer finalizes inside
the same transaction. Spec §§8.8, 13.3–13.4.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.domain.bootstrap import utc_z_optional
from app.domain.pact import (
    FINALIZE_FEEDBACK_SUMMARY,
    PACT_ANSWER_OPERATION,
    PACT_ANSWERED_EVENT,
    PACT_ANSWERED_GROWTH,
    PACT_COMPLETE_BOND_DELTA,
    PACT_COMPLETE_COMPLETENESS_MIN,
    PACT_COMPLETE_SESSION_MIN,
    PACT_COMPLETED_EVENT,
    PACT_COMPLETED_GROWTH,
    PACT_CREATED_EVENT,
    PACT_SESSION_OPERATION,
    PACT_SKIP_OPERATION,
    PactQuestion,
    StoredPactAnswer,
    answer_mistake,
    decode_session_answers,
    decode_session_questions,
    encode_session_answers,
    encode_session_feedback,
    encode_session_questions,
    find_stored_answer,
    incremental_answer_feedback,
    interview_session_questions,
    load_interview_bank,
    next_answer_question_id,
    next_completeness,
    next_skip_completeness,
    notes_session_questions,
    pact_answer_hash,
    pact_create_hash,
    pact_day_is_closed,
    pact_session_hash,
    pact_skip_hash,
    question_bank_version_for,
    scholar_mark_for_pact,
    session_date_for,
    session_day_index,
    session_explain,
    session_score,
    utc_stamp,
    week_window,
)
from app.domain.spirit import SpiritCreateResult
from app.providers.pact_feedback import PactFeedbackProvider, resolve_pact_question_feedback
from app.repositories import pact as pact_repo
from app.repositories import spirit as spirit_repo
from app.schemas.pact import (
    CreatePactRequest,
    PactAnswerFeedback,
    PactAnswerRequest,
    PactAnswerResource,
    PactMistakePublic,
    PactPublic,
    PactQuestionPublic,
    PactSessionPublic,
    PactSessionRequest,
    PactSessionStatus,
    PactSkippedSession,
    PactSkipRequest,
    PactStatus,
    PactTheme,
)
from app.schemas.spirit import MutationEvent, SpiritPublic
from app.services.evolution import grant_pact_scholar_mark, maybe_advance_stage
from app.services.growth import record_and_apply


@dataclass(frozen=True, slots=True)
class PactCreateSettlement:
    snapshot_version: int
    pact: PactPublic
    events: tuple[MutationEvent, ...]


@dataclass(frozen=True, slots=True)
class PactSessionSettlement:
    snapshot_version: int
    pact: PactPublic
    session: PactSessionPublic
    events: tuple[MutationEvent, ...]


@dataclass(frozen=True, slots=True)
class PactAnswerSettlement:
    snapshot_version: int
    pact: PactPublic
    resource: PactAnswerResource
    spirit: SpiritPublic | None
    events: tuple[MutationEvent, ...]


@dataclass(frozen=True, slots=True)
class PactSkipSettlement:
    snapshot_version: int
    pact: PactPublic
    resource: PactSkippedSession
    events: tuple[MutationEvent, ...]


def _api_error(
    code: str,
    *,
    status_code: int,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> ApiError:
    return ApiError(
        code,
        public_error_message(code),
        status_code=status_code,
        retryable=retryable,
        details=details,
    )


async def create_pact(
    session: AsyncSession,
    user: CurrentUser,
    body: CreatePactRequest,
    *,
    now: datetime,
) -> PactCreateSettlement:
    await pact_repo.assert_writable_transaction(session)
    load_interview_bank()
    locked = await pact_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    request_hash = pact_create_hash(
        theme=body.theme, title=body.title, notes_memory_id=body.notes_memory_id
    )
    claim = await pact_repo.claim_pact_idempotency(session, user.id, body.client_id, request_hash)
    if not claim.inserted:
        return await _replay(session, user, claim, request_hash, body.client_id)
    notes_memory_id = await _require_notes_memory(session, user, body)
    active = await pact_repo.fetch_active_pact(session, owner_id=user.id, spirit_id=locked.id)
    if active is not None:
        raise _api_error("PACT_ALREADY_ACTIVE", status_code=409)
    starts_at, ends_at, week_start = week_window(now, locked.timezone)
    try:
        row = await pact_repo.insert_pact(
            session,
            pact_id=uuid.uuid4(),
            owner_id=user.id,
            spirit_id=locked.id,
            client_id=body.client_id,
            theme=body.theme,
            title=body.title,
            notes_memory_id=notes_memory_id,
            question_bank_version=question_bank_version_for(body.theme),
            week_start=week_start,
            starts_at=starts_at,
            ends_at=ends_at,
        )
    except IntegrityError as exc:
        if pact_repo.is_active_pact_conflict(exc):
            raise _api_error("PACT_ALREADY_ACTIVE", status_code=409) from exc
        raise
    snapshot = await pact_repo.bump_spirit_version(session, owner_id=user.id, spirit_id=locked.id)
    await pact_repo.complete_pact_idempotency(session, user.id, body.client_id, row.id)
    return PactCreateSettlement(
        snapshot_version=snapshot,
        pact=_public_pact(row),
        events=(
            MutationEvent(id=uuid.uuid4(), type=PACT_CREATED_EVENT, occurred_at=utc_stamp(now)),
        ),
    )


async def _require_notes_memory(
    session: AsyncSession, user: CurrentUser, body: CreatePactRequest
) -> UUID | None:
    if body.theme != "notes":
        return None
    if body.notes_memory_id is None:
        raise _api_error("INVALID_INPUT", status_code=422)
    memory = await pact_repo.fetch_owned_notes_memory(
        session, owner_id=user.id, memory_id=body.notes_memory_id
    )
    if memory is None or memory.type != "knowledge" or memory.status != "active":
        raise _api_error("NOT_FOUND", status_code=404)
    return memory.id


async def _replay(
    session: AsyncSession,
    user: CurrentUser,
    claim: pact_repo.IdempotencyClaim,
    request_hash: str,
    client_id: UUID,
) -> PactCreateSettlement:
    if claim.request_hash != request_hash:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status == "in_progress":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    if claim.status == "conflict":
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status != "completed":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    row = None
    if claim.resource_id is not None:
        row = await pact_repo.fetch_pact_by_id(session, owner_id=user.id, pact_id=claim.resource_id)
    if row is None:
        row = await pact_repo.fetch_pact_by_client_id(
            session, owner_id=user.id, client_id=client_id
        )
    if row is None:
        raise _api_error("NOT_FOUND", status_code=404)
    locked = await pact_repo.lock_owned_spirit(session, user.id)
    snapshot = locked.version if locked is not None else row.version
    return PactCreateSettlement(
        snapshot_version=snapshot,
        pact=_public_pact(row),
        events=(),
    )


async def open_pact_session(
    session: AsyncSession,
    user: CurrentUser,
    body: PactSessionRequest,
    *,
    now: datetime,
) -> PactSessionSettlement:
    await pact_repo.assert_writable_transaction(session)
    locked = await pact_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    pact = await pact_repo.lock_owned_pact(session, owner_id=user.id, pact_id=body.pact_id)
    if pact is None:
        raise _api_error("NOT_FOUND", status_code=404)
    session_date = session_date_for(now, locked.timezone)
    request_hash = pact_session_hash(pact_id=body.pact_id, session_date=session_date)
    claim = await pact_repo.claim_pact_idempotency(
        session,
        user.id,
        body.client_id,
        request_hash,
        operation=PACT_SESSION_OPERATION,
    )
    if not claim.inserted:
        return await _replay_session(session, user, claim, request_hash, body, pact, locked.version)
    existing = await pact_repo.fetch_session_by_date(
        session, owner_id=user.id, pact_id=pact.id, session_date=session_date
    )
    if existing is not None:
        return await _finish_existing_session(
            session, user, body.client_id, pact, existing, locked.version
        )
    day_index = session_day_index(pact.week_start, session_date)
    if day_index is None or pact_day_is_closed(
        now=now, ends_at=pact.ends_at, status=pact.status, day_index=day_index
    ):
        raise _api_error("PACT_DAY_CLOSED", status_code=409)
    questions = await _session_questions(session, user, pact, day_index)
    inserted = True
    try:
        async with session.begin_nested():
            row = await pact_repo.insert_session(
                session,
                session_id=uuid.uuid4(),
                owner_id=user.id,
                pact_id=pact.id,
                session_date=session_date,
                day_index=day_index,
                client_id=body.client_id,
                explain=session_explain(pact.theme, questions),
                questions_json=encode_session_questions(questions),
            )
    except IntegrityError as exc:
        inserted = False
        row = await _session_after_conflict(session, user, body, pact, session_date, exc)
    if inserted:
        snapshot = await pact_repo.bump_spirit_version(
            session, owner_id=user.id, spirit_id=locked.id
        )
    else:
        snapshot = locked.version
    await pact_repo.complete_pact_idempotency(
        session,
        user.id,
        body.client_id,
        row.id,
        operation=PACT_SESSION_OPERATION,
        resource_type="pact_session",
    )
    return PactSessionSettlement(
        snapshot_version=snapshot,
        pact=_public_pact(pact),
        session=_public_session(row, pact.question_bank_version),
        events=(),
    )


async def _session_questions(
    session: AsyncSession,
    user: CurrentUser,
    pact: pact_repo.PactRow,
    day_index: int,
) -> tuple[PactQuestion, ...]:
    if pact.theme != "notes":
        load_interview_bank()
        return interview_session_questions(day_index)
    summary: str | None = None
    if pact.notes_memory_id is not None:
        memory = await pact_repo.fetch_owned_notes_memory(
            session, owner_id=user.id, memory_id=pact.notes_memory_id
        )
        if memory is not None and memory.type == "knowledge" and memory.status == "active":
            summary = memory.summary
    return notes_session_questions(day_index, summary)


async def _session_after_conflict(
    session: AsyncSession,
    user: CurrentUser,
    body: PactSessionRequest,
    pact: pact_repo.PactRow,
    session_date: date,
    exc: IntegrityError,
) -> pact_repo.PactSessionRow:
    if pact_repo.is_session_date_conflict(exc):
        existing = await pact_repo.fetch_session_by_date(
            session, owner_id=user.id, pact_id=pact.id, session_date=session_date
        )
        if existing is not None:
            return existing
    if pact_repo.is_session_client_conflict(exc):
        existing = await pact_repo.fetch_session_by_client_id(
            session, owner_id=user.id, pact_id=pact.id, client_id=body.client_id
        )
        if existing is None:
            raise _api_error("NOT_FOUND", status_code=404) from exc
        if existing.session_date != session_date:
            raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409) from exc
        return existing
    raise exc


async def _finish_existing_session(
    session: AsyncSession,
    user: CurrentUser,
    client_id: UUID,
    pact: pact_repo.PactRow,
    row: pact_repo.PactSessionRow,
    snapshot_version: int,
) -> PactSessionSettlement:
    await pact_repo.complete_pact_idempotency(
        session,
        user.id,
        client_id,
        row.id,
        operation=PACT_SESSION_OPERATION,
        resource_type="pact_session",
    )
    return PactSessionSettlement(
        snapshot_version=snapshot_version,
        pact=_public_pact(pact),
        session=_public_session(row, pact.question_bank_version),
        events=(),
    )


async def _replay_session(
    session: AsyncSession,
    user: CurrentUser,
    claim: pact_repo.IdempotencyClaim,
    request_hash: str,
    body: PactSessionRequest,
    pact: pact_repo.PactRow,
    snapshot_version: int,
) -> PactSessionSettlement:
    if claim.request_hash != request_hash:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status == "in_progress":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    if claim.status == "conflict":
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status != "completed":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    row = None
    if claim.resource_id is not None:
        row = await pact_repo.fetch_session_by_id(
            session, owner_id=user.id, session_id=claim.resource_id
        )
    if row is None:
        row = await pact_repo.fetch_session_by_client_id(
            session, owner_id=user.id, pact_id=body.pact_id, client_id=body.client_id
        )
    if row is None:
        raise _api_error("NOT_FOUND", status_code=404)
    return PactSessionSettlement(
        snapshot_version=snapshot_version,
        pact=_public_pact(pact),
        session=_public_session(row, pact.question_bank_version),
        events=(),
    )


async def submit_pact_answer(
    session: AsyncSession,
    user: CurrentUser,
    body: PactAnswerRequest,
    *,
    now: datetime,
    feedback_provider: PactFeedbackProvider | None = None,
) -> PactAnswerSettlement:
    await pact_repo.assert_writable_transaction(session)
    locked = await pact_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    preview = await pact_repo.fetch_session_by_id(
        session, owner_id=user.id, session_id=body.session_id
    )
    if preview is None:
        raise _api_error("NOT_FOUND", status_code=404)
    pact = await pact_repo.lock_owned_pact(session, owner_id=user.id, pact_id=preview.pact_id)
    if pact is None:
        raise _api_error("NOT_FOUND", status_code=404)
    row = await pact_repo.lock_owned_session(session, owner_id=user.id, session_id=body.session_id)
    if row is None:
        raise _api_error("NOT_FOUND", status_code=404)
    request_hash = pact_answer_hash(
        session_id=body.session_id, question_id=body.question_id, text=body.text
    )
    claim = await pact_repo.claim_pact_idempotency(
        session,
        user.id,
        body.client_id,
        request_hash,
        operation=PACT_ANSWER_OPERATION,
    )
    questions = decode_session_questions(row.questions)
    answers = decode_session_answers(row.answers)
    if not claim.inserted:
        return await _replay_answer(
            session,
            user,
            claim,
            request_hash,
            body,
            pact,
            row,
            questions,
            answers,
            locked.id,
            snapshot_version=locked.version,
            now=now,
        )
    if body.question_id not in {item.question_id for item in questions}:
        raise _api_error("PACT_QUESTION_NOT_FOUND", status_code=404)
    existing = find_stored_answer(answers, question_id=body.question_id)
    if existing is not None:
        raise _api_error("PACT_QUESTION_ALREADY_ANSWERED", status_code=409)
    if row.status != "ready":
        raise _api_error("CONFLICT", status_code=409)
    if pact.status != "active":
        raise _api_error("CONFLICT", status_code=409)
    if body.expected_session_version != row.version:
        raise _api_error("CONFLICT", status_code=409)
    expected_question = next_answer_question_id(questions, answers)
    if expected_question is None:
        raise _api_error("PACT_QUESTION_ALREADY_ANSWERED", status_code=409)
    if body.question_id != expected_question:
        raise _api_error("INVALID_INPUT", status_code=422)
    last_question = expected_question == questions[-1].question_id
    question = next(item for item in questions if item.question_id == body.question_id)
    if last_question:
        feedback = incremental_answer_feedback(fallback=False)
    else:
        feedback = await resolve_pact_question_feedback(
            feedback_provider,
            question_id=question.question_id,
            question_text=question.text,
            answer_text=body.text,
        )
    stored = StoredPactAnswer(
        question_id=body.question_id,
        client_id=body.client_id,
        text=body.text,
        request_hash=request_hash,
        feedback=feedback,
    )
    merged = (*answers, stored)
    if last_question:
        scored = session_score(merged)
        updated = await pact_repo.save_last_answer_and_finalize(
            session,
            owner_id=user.id,
            session_id=row.id,
            expected_version=row.version,
            answers_json=encode_session_answers(merged),
            score=scored,
            feedback_json=encode_session_feedback(FINALIZE_FEEDBACK_SUMMARY),
            now=now,
        )
        if updated is None:
            raise _api_error("CONFLICT", status_code=409)
        settlement = await _apply_finalize_side_effects(
            session,
            user,
            pact=pact,
            row=updated,
            stored=stored,
            answers=decode_session_answers(updated.answers),
            questions=questions,
            spirit_id=locked.id,
            snapshot_version=locked.version,
            now=now,
            won=True,
        )
        await pact_repo.complete_pact_idempotency(
            session,
            user.id,
            body.client_id,
            updated.id,
            operation=PACT_ANSWER_OPERATION,
            resource_type="pact_session",
        )
        return settlement
    updated = await pact_repo.save_session_answers(
        session,
        owner_id=user.id,
        session_id=row.id,
        expected_version=row.version,
        answers_json=encode_session_answers(merged),
    )
    if updated is None:
        raise _api_error("CONFLICT", status_code=409)
    saved_answers = decode_session_answers(updated.answers)
    snapshot = await pact_repo.bump_spirit_version(session, owner_id=user.id, spirit_id=locked.id)
    await pact_repo.complete_pact_idempotency(
        session,
        user.id,
        body.client_id,
        updated.id,
        operation=PACT_ANSWER_OPERATION,
        resource_type="pact_session",
    )
    return _answer_settlement(pact, updated, stored, saved_answers, snapshot)


async def _replay_answer(
    session: AsyncSession,
    user: CurrentUser,
    claim: pact_repo.IdempotencyClaim,
    request_hash: str,
    body: PactAnswerRequest,
    pact: pact_repo.PactRow,
    row: pact_repo.PactSessionRow,
    questions: tuple[PactQuestion, ...],
    answers: tuple[StoredPactAnswer, ...],
    spirit_id: uuid.UUID,
    *,
    snapshot_version: int,
    now: datetime,
) -> PactAnswerSettlement:
    existing = find_stored_answer(answers, question_id=body.question_id)
    if claim.request_hash != request_hash:
        if existing is not None:
            raise _api_error("PACT_QUESTION_ALREADY_ANSWERED", status_code=409)
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status == "in_progress":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    if claim.status == "conflict":
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status != "completed":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    stored = existing
    if stored is None:
        for item in answers:
            if item.client_id == body.client_id:
                stored = item
                break
    if stored is None:
        raise _api_error("NOT_FOUND", status_code=404)
    if stored.question_id != body.question_id or stored.text != body.text:
        raise _api_error("PACT_QUESTION_ALREADY_ANSWERED", status_code=409)
    if len(answers) == 3 and body.question_id == questions[-1].question_id:
        won = False
        if row.status == "ready":
            scored = session_score(answers)
            cas = await pact_repo.save_last_answer_and_finalize(
                session,
                owner_id=user.id,
                session_id=row.id,
                expected_version=row.version,
                answers_json=encode_session_answers(answers),
                score=scored,
                feedback_json=encode_session_feedback(FINALIZE_FEEDBACK_SUMMARY),
                now=now,
            )
            if cas is None:
                raise _api_error("CONFLICT", status_code=409)
            row = cas
            won = True
        return await _apply_finalize_side_effects(
            session,
            user,
            pact=pact,
            row=row,
            stored=stored,
            answers=answers,
            questions=questions,
            spirit_id=spirit_id,
            snapshot_version=snapshot_version,
            now=now,
            won=won,
        )
    return _answer_settlement(pact, row, stored, answers, snapshot_version)


async def _apply_finalize_side_effects(
    session: AsyncSession,
    user: CurrentUser,
    *,
    pact: pact_repo.PactRow,
    row: pact_repo.PactSessionRow,
    stored: StoredPactAnswer,
    answers: tuple[StoredPactAnswer, ...],
    questions: tuple[PactQuestion, ...],
    spirit_id: uuid.UUID,
    snapshot_version: int,
    now: datetime,
    won: bool,
) -> PactAnswerSettlement:
    events: list[MutationEvent] = []
    spirit_public: SpiritPublic | None = None
    snapshot = snapshot_version
    if won:
        scored = session_score(answers)
        for question in questions:
            answer = find_stored_answer(answers, question_id=question.question_id)
            if answer is None:
                continue
            mistake = answer_mistake(question, answer)
            if mistake is None:
                continue
            category, summary = mistake
            await pact_repo.insert_session_mistake(
                session,
                owner_id=user.id,
                pact_id=pact.id,
                session_id=row.id,
                question_id=question.question_id,
                category=category,
                summary=summary,
            )
        completeness = next_completeness(
            previous=pact.completeness,
            completed_before=pact.completed_sessions,
            scored=scored,
        )
        updated_pact = await pact_repo.apply_completed_session(
            session, owner_id=user.id, pact_id=pact.id, completeness=completeness
        )
        if updated_pact is None:
            raise _api_error("CONFLICT", status_code=409)
        pact = updated_pact
        answered = await record_and_apply(
            session,
            owner_id=user.id,
            spirit_id=spirit_id,
            event_type=PACT_ANSWERED_GROWTH,
            source_id=row.id,
            payload={},
            now=now,
            touch_interact=True,
        )
        events.append(
            MutationEvent(
                id=answered.event_id,
                type=PACT_ANSWERED_EVENT,
                occurred_at=utc_stamp(now),
            )
        )
        completed = await pact_repo.complete_pact_if_eligible(
            session,
            owner_id=user.id,
            pact_id=pact.id,
            scholar_mark=scholar_mark_for_pact(
                theme=pact.theme, question_bank_version=pact.question_bank_version
            ),
            now=now,
            min_sessions=PACT_COMPLETE_SESSION_MIN,
            min_completeness=PACT_COMPLETE_COMPLETENESS_MIN,
        )
        if completed is not None:
            pact = completed
            await grant_pact_scholar_mark(
                session,
                owner_id=user.id,
                spirit_id=spirit_id,
                theme=pact.theme,
                question_bank_version=pact.question_bank_version.removeprefix(f"{pact.theme}-"),
            )
            finished = await record_and_apply(
                session,
                owner_id=user.id,
                spirit_id=spirit_id,
                event_type=PACT_COMPLETED_GROWTH,
                source_id=pact.id,
                payload={"bond_delta": PACT_COMPLETE_BOND_DELTA},
                now=now,
                touch_interact=True,
            )
            events.append(
                MutationEvent(
                    id=finished.event_id,
                    type=PACT_COMPLETED_EVENT,
                    occurred_at=utc_stamp(now),
                )
            )
            await maybe_advance_stage(session, owner_id=user.id, spirit_id=spirit_id, now=now)
        if answered.spirit is not None:
            snapshot = answered.spirit.version
    mistakes = await pact_repo.list_session_mistakes(session, owner_id=user.id, session_id=row.id)
    if pact.status == "completed":
        fetched = await spirit_repo.fetch_create_result(session, user.id)
        if fetched is not None:
            spirit_public = _spirit_public(fetched)
            snapshot = fetched.version
    return PactAnswerSettlement(
        snapshot_version=snapshot,
        pact=_public_pact(pact),
        resource=_public_answer(
            row,
            stored,
            answers,
            finalized=True,
            score=int(row.score or session_score(answers)),
            pact_status=cast(PactStatus, pact.status),
            mistakes=mistakes,
        ),
        spirit=spirit_public,
        events=tuple(events),
    )


def _answer_settlement(
    pact: pact_repo.PactRow,
    row: pact_repo.PactSessionRow,
    stored: StoredPactAnswer,
    answers: tuple[StoredPactAnswer, ...],
    snapshot_version: int,
) -> PactAnswerSettlement:
    return PactAnswerSettlement(
        snapshot_version=snapshot_version,
        pact=_public_pact(pact),
        resource=_public_answer(row, stored, answers),
        spirit=None,
        events=(),
    )


def _public_answer(
    row: pact_repo.PactSessionRow,
    stored: StoredPactAnswer,
    answers: tuple[StoredPactAnswer, ...],
    *,
    finalized: bool = False,
    score: int | None = None,
    pact_status: PactStatus | None = None,
    mistakes: tuple[pact_repo.PactMistakeRow, ...] = (),
) -> PactAnswerResource:
    feedback = stored.feedback
    if finalized:
        feedback_summary = FINALIZE_FEEDBACK_SUMMARY
        improvements: list[str] = []
    else:
        feedback_summary = feedback.summary
        improvements = list(feedback.improvements)
    return PactAnswerResource(
        type="pact_answer",
        session_id=row.id,
        question_id=stored.question_id,
        feedback=PactAnswerFeedback(summary=feedback_summary, improvements=improvements),
        answered_count=len(answers),
        row_version=row.version,
        finalized=finalized,
        score=score,
        pact_status=pact_status,
        mistakes=[
            PactMistakePublic(
                question_id=item.question_id,
                category=item.category,
                summary=item.summary,
                times_seen=item.times_seen,
            )
            for item in mistakes
        ],
    )


def _spirit_public(result: SpiritCreateResult) -> SpiritPublic:
    return SpiritPublic.model_validate(
        {
            "id": result.spirit_id,
            "name": result.name,
            "egg": result.egg,
            "invite_code": result.invite_code,
            "closeness": result.closeness,
            "curiosity": result.curiosity,
            "sharpness": result.sharpness,
            "nocturnal": result.nocturnal,
            "stubborn": result.stubborn,
            "hunger": result.hunger,
            "energy": result.energy,
            "mood": result.mood,
            "bond": result.bond,
            "stage": result.stage,
            "status": result.status,
            "scholar_marks": list(result.scholar_marks),
            "version": result.version,
            "onboarding_step": result.onboarding_step,
            "onboarding_completed_at": utc_z_optional(result.onboarding_completed_at),
            "hatched_at": utc_z_optional(result.hatched_at),
            "created_at": utc_stamp(result.created_at),
        }
    )


async def submit_pact_skip(
    session: AsyncSession,
    user: CurrentUser,
    body: PactSkipRequest,
    *,
    now: datetime,
) -> PactSkipSettlement:
    await pact_repo.assert_writable_transaction(session)
    locked = await pact_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    pact = await pact_repo.lock_owned_pact(session, owner_id=user.id, pact_id=body.pact_id)
    if pact is None:
        raise _api_error("NOT_FOUND", status_code=404)
    today = session_date_for(now, locked.timezone)
    if body.session_date > today:
        raise _api_error("INVALID_INPUT", status_code=422)
    day_index = session_day_index(pact.week_start, body.session_date)
    if day_index is None or pact_day_is_closed(
        now=now, ends_at=pact.ends_at, status=pact.status, day_index=day_index
    ):
        raise _api_error("PACT_DAY_CLOSED", status_code=409)
    request_hash = pact_skip_hash(pact_id=body.pact_id, session_date=body.session_date)
    claim = await pact_repo.claim_pact_idempotency(
        session,
        user.id,
        body.client_id,
        request_hash,
        operation=PACT_SKIP_OPERATION,
    )
    if not claim.inserted:
        return await _replay_skip(session, user, claim, request_hash, body, pact, locked.version)
    existing = await pact_repo.lock_owned_session_by_date(
        session, owner_id=user.id, pact_id=pact.id, session_date=body.session_date
    )
    if existing is not None:
        return await _skip_existing_session(
            session, user, body, pact, existing, locked.id, locked.version, now=now
        )
    questions = await _session_questions(session, user, pact, day_index)
    try:
        async with session.begin_nested():
            row = await pact_repo.insert_skipped_session(
                session,
                session_id=uuid.uuid4(),
                owner_id=user.id,
                pact_id=pact.id,
                session_date=body.session_date,
                day_index=day_index,
                client_id=body.client_id,
                explain=session_explain(pact.theme, questions),
                questions_json=encode_session_questions(questions),
                now=now,
            )
    except IntegrityError as exc:
        if not pact_repo.is_session_date_conflict(exc):
            raise
        raced = await pact_repo.lock_owned_session_by_date(
            session, owner_id=user.id, pact_id=pact.id, session_date=body.session_date
        )
        if raced is None:
            raise _api_error("NOT_FOUND", status_code=404) from exc
        return await _skip_existing_session(
            session, user, body, pact, raced, locked.id, locked.version, now=now
        )
    completeness = next_skip_completeness(
        previous=pact.completeness,
        completed_sessions=pact.completed_sessions,
        skipped_before=pact.skipped_sessions,
    )
    updated = await pact_repo.apply_skipped_session(
        session, owner_id=user.id, pact_id=pact.id, completeness=completeness
    )
    if updated is None:
        raise _api_error("CONFLICT", status_code=409)
    snapshot = await pact_repo.bump_spirit_version(session, owner_id=user.id, spirit_id=locked.id)
    await pact_repo.complete_pact_idempotency(
        session,
        user.id,
        body.client_id,
        row.id,
        operation=PACT_SKIP_OPERATION,
        resource_type="pact_session",
    )
    return PactSkipSettlement(
        snapshot_version=snapshot,
        pact=_public_pact(updated),
        resource=_public_skipped(row),
        events=(),
    )


async def _skip_existing_session(
    session: AsyncSession,
    user: CurrentUser,
    body: PactSkipRequest,
    pact: pact_repo.PactRow,
    row: pact_repo.PactSessionRow,
    spirit_id: uuid.UUID,
    snapshot_version: int,
    *,
    now: datetime,
) -> PactSkipSettlement:
    if row.status == "answered":
        raise _api_error("PACT_SESSION_ALREADY_SUBMITTED", status_code=409)
    if row.status == "skipped":
        await pact_repo.complete_pact_idempotency(
            session,
            user.id,
            body.client_id,
            row.id,
            operation=PACT_SKIP_OPERATION,
            resource_type="pact_session",
        )
        return PactSkipSettlement(
            snapshot_version=snapshot_version,
            pact=_public_pact(pact),
            resource=_public_skipped(row),
            events=(),
        )
    if row.status != "ready":
        raise _api_error("CONFLICT", status_code=409)
    skipped = await pact_repo.skip_ready_session(
        session, owner_id=user.id, session_id=row.id, now=now
    )
    if skipped is None:
        current = await pact_repo.lock_owned_session_by_date(
            session, owner_id=user.id, pact_id=pact.id, session_date=body.session_date
        )
        if current is not None and current.status == "answered":
            raise _api_error("PACT_SESSION_ALREADY_SUBMITTED", status_code=409)
        raise _api_error("CONFLICT", status_code=409)
    completeness = next_skip_completeness(
        previous=pact.completeness,
        completed_sessions=pact.completed_sessions,
        skipped_before=pact.skipped_sessions,
    )
    updated = await pact_repo.apply_skipped_session(
        session, owner_id=user.id, pact_id=pact.id, completeness=completeness
    )
    if updated is None:
        raise _api_error("CONFLICT", status_code=409)
    snapshot = await pact_repo.bump_spirit_version(session, owner_id=user.id, spirit_id=spirit_id)
    await pact_repo.complete_pact_idempotency(
        session,
        user.id,
        body.client_id,
        skipped.id,
        operation=PACT_SKIP_OPERATION,
        resource_type="pact_session",
    )
    return PactSkipSettlement(
        snapshot_version=snapshot,
        pact=_public_pact(updated),
        resource=_public_skipped(skipped),
        events=(),
    )


async def _replay_skip(
    session: AsyncSession,
    user: CurrentUser,
    claim: pact_repo.IdempotencyClaim,
    request_hash: str,
    body: PactSkipRequest,
    pact: pact_repo.PactRow,
    snapshot_version: int,
) -> PactSkipSettlement:
    if claim.request_hash != request_hash:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status == "in_progress":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    if claim.status == "conflict":
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status != "completed":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    row = None
    if claim.resource_id is not None:
        row = await pact_repo.fetch_session_by_id(
            session, owner_id=user.id, session_id=claim.resource_id
        )
    if row is None:
        row = await pact_repo.fetch_session_by_date(
            session, owner_id=user.id, pact_id=body.pact_id, session_date=body.session_date
        )
    if row is None:
        raise _api_error("NOT_FOUND", status_code=404)
    if row.status != "skipped":
        raise _api_error("PACT_SESSION_ALREADY_SUBMITTED", status_code=409)
    return PactSkipSettlement(
        snapshot_version=snapshot_version,
        pact=_public_pact(pact),
        resource=_public_skipped(row),
        events=(),
    )


def _public_skipped(row: pact_repo.PactSessionRow) -> PactSkippedSession:
    return PactSkippedSession(
        type="pact_session",
        id=row.id,
        pact_id=row.pact_id,
        date=row.session_date,
        day_index=row.day_index,
        status="skipped",
        row_version=row.version,
    )


def _public_pact(row: pact_repo.PactRow) -> PactPublic:
    if row.theme not in {"interview", "notes"}:
        raise RuntimeError("pact theme is not public")
    if row.status not in {"active", "completed", "abandoned"}:
        raise RuntimeError("pact status is not public")
    return PactPublic(
        type="pact",
        id=row.id,
        theme=cast(PactTheme, row.theme),
        title=row.title,
        notes_memory_id=row.notes_memory_id,
        question_bank_version=row.question_bank_version,
        status=cast(PactStatus, row.status),
        completed_sessions=row.completed_sessions,
        skipped_sessions=row.skipped_sessions,
        completeness=row.completeness,
        scholar_mark=row.scholar_mark,
        week_start=row.week_start,
        starts_at=utc_stamp(row.starts_at),
        ends_at=utc_stamp(row.ends_at),
        version=row.version,
        created_at=utc_stamp(row.created_at),
    )


def _public_session(row: pact_repo.PactSessionRow, question_bank_version: str) -> PactSessionPublic:
    if row.status not in {"ready", "answered", "skipped", "closed"}:
        raise RuntimeError("pact session status is not public")
    questions = decode_session_questions(row.questions)
    return PactSessionPublic(
        type="pact_session",
        id=row.id,
        pact_id=row.pact_id,
        date=row.session_date,
        day_index=row.day_index,
        explain=row.explain,
        questions=[
            PactQuestionPublic(question_id=item.question_id, text=item.text) for item in questions
        ],
        question_bank_version=question_bank_version,
        status=cast(PactSessionStatus, row.status),
        row_version=row.version,
    )
