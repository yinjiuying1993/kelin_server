"""account.delete worker: Storage → Auth → DB. Spec §14.9."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import claimed_transaction
from app.domain.account import ACCOUNT_DELETE_EVENT, ACCOUNT_DELETE_MAX_ATTEMPTS, STORAGE_PAGE
from app.domain.outbox import DEFAULT_OUTBOX_LEASE_SECONDS
from app.domain.spirit_state import require_aware
from app.integrations.account_storage import AccountObjectCatalog, AccountStorageError
from app.integrations.auth_admin import AuthAdminError, InProcessAuthAdmin
from app.integrations.storage import PrivateSightStorage, PrivateTtsStorage
from app.repositories import account as account_repo
from app.repositories import outbox as outbox_repo

_WORKER_ROLE = "kelin_worker"


@dataclass(frozen=True, slots=True)
class AccountDeleteTick:
    deletion_id: UUID
    job_id: UUID
    status: str
    outcome: str


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = getattr(exc, "orig", None)
    state = getattr(orig, "sqlstate", None)
    if state:
        return str(state)
    wrapped = getattr(orig, "__cause__", None)
    state = getattr(wrapped, "sqlstate", None)
    return str(state) if state else None


def _cursor_of(ref) -> str:
    return f"{ref.bucket}:{ref.object_path}"


async def process_due_account_deletes(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    settings: Settings | None = None,
    catalog: AccountObjectCatalog | None = None,
    auth_admin: InProcessAuthAdmin | None = None,
    sight_storage: PrivateSightStorage | None = None,
    tts_storage: PrivateTtsStorage | None = None,
) -> list[AccountDeleteTick]:
    require_aware(now, field="now")
    resolved = settings or get_settings()
    objects = catalog or AccountObjectCatalog(
        sight_storage or PrivateSightStorage(),
        tts_storage or PrivateTtsStorage(),
    )
    admin = auth_admin or InProcessAuthAdmin(resolved.sqlalchemy_async_url())
    worker_id = f"account-delete:{uuid4()}"
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        jobs = await outbox_repo.claim_due_outbox(
            session,
            now=now,
            worker_id=worker_id,
            lease_seconds=resolved.outbox_lease_seconds or DEFAULT_OUTBOX_LEASE_SECONDS,
            event_types=(ACCOUNT_DELETE_EVENT,),
        )
    ticks: list[AccountDeleteTick] = []
    for job in jobs:
        raw_id = job.payload.get("deletion_id")
        try:
            deletion_id = UUID(str(raw_id))
        except (ValueError, TypeError, AttributeError):
            deletion_id = job.aggregate_id
        try:
            status = await _run_one(
                factory,
                deletion_id=deletion_id,
                job_id=job.id,
                locked_by=job.locked_by,
                now=now,
                objects=objects,
                admin=admin,
            )
            ticks.append(
                AccountDeleteTick(
                    deletion_id=deletion_id,
                    job_id=job.id,
                    status=status,
                    outcome="done" if status == "completed" else "retry",
                )
            )
        except (AccountStorageError, AuthAdminError, DBAPIError) as exc:
            error = "ACCOUNT_DELETE_RETRY"
            if isinstance(exc, AccountStorageError):
                error = "ACCOUNT_STORAGE_RETRY"
            elif isinstance(exc, AuthAdminError):
                error = "ACCOUNT_AUTH_RETRY"
            else:
                error = _sqlstate(exc) or error
            status = await _fail(
                factory,
                deletion_id=deletion_id,
                job_id=job.id,
                locked_by=job.locked_by,
                now=now,
                error=error,
            )
            ticks.append(
                AccountDeleteTick(
                    deletion_id=deletion_id,
                    job_id=job.id,
                    status=status,
                    outcome="dead" if status == "dead" else "retry",
                )
            )
    return ticks


async def _run_one(
    factory: async_sessionmaker[AsyncSession],
    *,
    deletion_id: UUID,
    job_id: UUID,
    locked_by: str,
    now: datetime,
    objects: AccountObjectCatalog,
    admin: InProcessAuthAdmin,
) -> str:
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        row = await account_repo.load_claimed(session, deletion_id)
        if row is None or row.status == "completed":
            await outbox_repo.complete_outbox_job(
                session, job_id=job_id, locked_by=locked_by, now=now
            )
            return "completed"
        if row.status == "dead":
            await outbox_repo.complete_outbox_job(
                session, job_id=job_id, locked_by=locked_by, now=now
            )
            return "dead"
        owner_id = row.owner_id
        cursor = row.storage_cursor
        attempts = row.attempts
        status = "deleting_storage" if row.status in {"accepted", "retry"} else row.status
        if status == "deleting_storage":
            await account_repo.save_progress(
                session,
                deletion_id=row.id,
                status="deleting_storage",
                storage_cursor=cursor,
                attempts=attempts,
                last_error_code=None,
            )
    if status == "deleting_storage":
        cursor = await _delete_storage_page(objects, owner_id=owner_id, after=cursor)
        async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
            if cursor is not None:
                await account_repo.save_progress(
                    session,
                    deletion_id=deletion_id,
                    status="deleting_storage",
                    storage_cursor=cursor,
                    attempts=attempts,
                    last_error_code=None,
                )
                await outbox_repo.release_outbox_until(
                    session,
                    job_id=job_id,
                    locked_by=locked_by,
                    available_at=now,
                    now=now,
                )
                return "deleting_storage"
            await account_repo.save_progress(
                session,
                deletion_id=deletion_id,
                status="deleting_auth",
                storage_cursor=None,
                attempts=attempts,
                last_error_code=None,
            )
            status = "deleting_auth"
    if status == "deleting_auth":
        await admin.delete_user(owner_id)
        async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
            await account_repo.save_progress(
                session,
                deletion_id=deletion_id,
                status="deleting_database",
                storage_cursor=None,
                attempts=attempts,
                last_error_code=None,
            )
            status = "deleting_database"
    if status == "deleting_database":
        async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
            leftover = await account_repo.residue_exists(session, owner_id)
            if leftover:
                raise AuthAdminError("business residue remains")
            await account_repo.save_progress(
                session,
                deletion_id=deletion_id,
                status="completed",
                storage_cursor=None,
                attempts=attempts,
                last_error_code=None,
                completed_at=now,
            )
            await outbox_repo.complete_outbox_job(
                session, job_id=job_id, locked_by=locked_by, now=now
            )
            return "completed"
    return status


async def _delete_storage_page(
    objects: AccountObjectCatalog,
    *,
    owner_id: UUID,
    after: str | None,
) -> str | None:
    page = objects.list_owner_page(owner_id, after=after, limit=STORAGE_PAGE)
    if not page:
        return None
    last = after
    try:
        for ref in page:
            objects.delete_object(ref)
            last = _cursor_of(ref)
    except Exception as exc:
        raise AccountStorageError("storage delete failed") from exc
    remaining = objects.list_owner_page(owner_id, after=last, limit=1)
    return last if remaining else None


async def _fail(
    factory: async_sessionmaker[AsyncSession],
    *,
    deletion_id: UUID,
    job_id: UUID,
    locked_by: str,
    now: datetime,
    error: str,
) -> str:
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        row = await account_repo.load_claimed(session, deletion_id)
        attempts = (row.attempts if row is not None else 0) + 1
        dead = attempts >= ACCOUNT_DELETE_MAX_ATTEMPTS
        status = "dead" if dead else "retry"
        if row is not None:
            await account_repo.save_progress(
                session,
                deletion_id=deletion_id,
                status=status,
                storage_cursor=row.storage_cursor,
                attempts=attempts,
                last_error_code=error,
                dead_at=now if dead else None,
            )
        if dead:
            await outbox_repo.complete_outbox_job(
                session, job_id=job_id, locked_by=locked_by, now=now
            )
        else:
            await outbox_repo.fail_outbox_job(
                session,
                job_id=job_id,
                locked_by=locked_by,
                now=now,
                error=error,
            )
        return status
