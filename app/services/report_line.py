"""POST /report/line: retry signature_line only. Spec §14.8."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.report import LINE_MAX_ATTEMPTS, report_line_request_hash
from app.domain.spirit_state import require_aware
from app.providers.errors import ProviderCancelled, ProviderError
from app.providers.report_line import (
    ReportLineInput,
    ReportLineProvider,
    build_report_line_provider,
)
from app.repositories import report as report_repo
from app.schemas.report import ReportLinePatch, ReportLineRequest, ReportLineResult, ReportSnapshot
from app.services.report_project import project_snapshot


def _api_error(code: str, *, status_code: int, retryable: bool = False) -> ApiError:
    return ApiError(
        code,
        public_error_message(code),
        status_code=status_code,
        retryable=retryable,
    )


def _line_input(snapshot: ReportSnapshot) -> ReportLineInput:
    card = snapshot.card
    if card is None:
        raise _api_error("REPORT_LINE_UNAVAILABLE", status_code=409)
    return ReportLineInput(
        title=card.title,
        weather=card.room_weather,
        traits=[item.dimension for item in card.top_traits[:2]],
        marks=list(card.scholar_marks),
    )


async def _result(
    session: AsyncSession,
    user: CurrentUser,
    *,
    now: datetime,
    report_id: UUID,
) -> ReportLineResult:
    locked = await report_repo.lock_owned_spirit(session, user.id)
    row = await report_repo.fetch_owned_report_by_id(session, user.id, report_id)
    if locked is None or row is None:
        raise _api_error("NOT_FOUND", status_code=404)
    live = {}
    if row.top_memory_ids:
        live = await report_repo.load_memories_for_ids(
            session, owner_id=user.id, memory_ids=row.top_memory_ids
        )
    snapshot = project_snapshot(
        hatched_at=locked.hatched_at,
        ordinary_dialogue_rounds=locked.ordinary_dialogue_rounds,
        now=now,
        row=row,
        live_memories=live,
    )
    if snapshot.card is None:
        raise _api_error("REPORT_LINE_UNAVAILABLE", status_code=409)
    return ReportLineResult(
        resource=snapshot.card,
        patch=ReportLinePatch(snapshot_version=locked.version, report=snapshot),
    )


async def retry_report_line(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    body: ReportLineRequest,
    *,
    now: datetime,
    settings: Settings | None = None,
    line_provider: ReportLineProvider | None = None,
) -> ReportLineResult:
    require_aware(now, field="now")
    resolved = settings or get_settings()
    provider = line_provider or build_report_line_provider(resolved)
    request_hash = report_line_request_hash(
        report_id=body.report_id, expected_version=body.expected_version
    )
    reserved_version: int | None = None
    pending: ReportSnapshot | None = None
    async with claimed_transaction(factory, user) as session:
        await report_repo.assert_writable_transaction(session)
        claim = await report_repo.claim_line_idempotency(
            session, user.id, body.client_id, request_hash
        )
        if not claim.inserted:
            if claim.request_hash != request_hash:
                raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
            if claim.status != "completed":
                raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
            return await _result(session, user, now=now, report_id=body.report_id)
        locked = await report_repo.lock_owned_spirit(session, user.id)
        row = await report_repo.fetch_owned_report_by_id(
            session, user.id, body.report_id, for_update=True
        )
        if locked is None or row is None:
            raise _api_error("NOT_FOUND", status_code=404)
        if (
            row.status != "partial"
            or row.signature_line is not None
            or row.line_attempts >= LINE_MAX_ATTEMPTS
        ):
            raise _api_error("REPORT_LINE_UNAVAILABLE", status_code=409)
        if row.version != body.expected_version:
            raise _api_error("CONFLICT", status_code=409)
        reserved = await report_repo.reserve_line_attempt(
            session,
            owner_id=user.id,
            report_id=body.report_id,
            expected_version=body.expected_version,
        )
        if reserved is None:
            raise _api_error("CONFLICT", status_code=409)
        await report_repo.bump_spirit_version(session, owner_id=user.id, spirit_id=locked.id)
        live = {}
        if reserved.top_memory_ids:
            live = await report_repo.load_memories_for_ids(
                session, owner_id=user.id, memory_ids=reserved.top_memory_ids
            )
        pending = project_snapshot(
            hatched_at=locked.hatched_at,
            ordinary_dialogue_rounds=locked.ordinary_dialogue_rounds,
            now=now,
            row=reserved,
            live_memories=live,
        )
        reserved_version = reserved.version
    signature_line: str | None = None
    if pending is not None:
        try:
            output = await provider.generate(_line_input(pending))
            text = output.text.strip()
            if text:
                signature_line = text
        except (ProviderError, ProviderCancelled):
            signature_line = None
    async with claimed_transaction(factory, user) as session:
        if signature_line and reserved_version is not None:
            saved = await report_repo.save_signature_line(
                session,
                owner_id=user.id,
                report_id=body.report_id,
                expected_version=reserved_version,
                signature_line=signature_line,
            )
            if saved is not None:
                locked = await report_repo.lock_owned_spirit(session, user.id)
                if locked is not None:
                    await report_repo.bump_spirit_version(
                        session, owner_id=user.id, spirit_id=locked.id
                    )
        await report_repo.complete_line_idempotency(
            session, user.id, body.client_id, body.report_id
        )
        return await _result(session, user, now=now, report_id=body.report_id)
