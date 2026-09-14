"""P03-T01 RLS matrix. Spec §§4.3, 6.3–6.5, 7.1–7.5.

T02 applies ENABLE/FORCE RLS, CREATE POLICY, and minimum grants from this module.
Client roles (anon/authenticated) never receive policies or grants.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class Command(StrEnum):
    SELECT = "SELECT"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class OwnerKind(StrEnum):
    USER_COLUMN = "user_column"
    SPIRIT_OWNER = "spirit_owner"
    USER_AND_SPIRIT = "user_and_spirit"
    PACT_OWNER = "pact_owner"
    FRIEND_PARTICIPANT = "friend_participant"
    VISIT_PARTY = "visit_party"
    POSTCARD_RECEIVER = "postcard_receiver"
    CATALOG = "catalog"
    INTERNAL_WORKER = "internal_worker"
    INTERNAL_OUTBOX = "internal_outbox"


class WritePath(StrEnum):
    OWNER_DML = "owner_dml"
    API_OWNER_WRITE = "api_owner_write"
    SECURITY_DEFINER = "security_definer"
    WORKER_INTERNAL = "worker_internal"
    CATALOG_READ = "catalog_read"
    RECEIVER_READ_WORKER_WRITE = "receiver_read_worker_write"


Expectation = Literal["allow", "empty", "deny"]


@dataclass(frozen=True)
class RlsTestCase:
    name: str
    actor: str
    command: Command
    expected: Expectation
    notes: str = ""
    requires: Literal["table_policy", "security_definer"] = "table_policy"


@dataclass(frozen=True)
class RoleAccess:
    select: bool = False
    insert: bool = False
    update: bool = False
    delete: bool = False
    using_sql: str | None = None
    with_check_sql: str | None = None
    update_columns: tuple[str, ...] | None = None


@dataclass(frozen=True)
class PolicyBlueprint:
    name: str
    table: str
    command: str
    roles: tuple[str, ...]
    using_sql: str | None
    with_check_sql: str | None


@dataclass(frozen=True)
class RlsTableExpectation:
    table: str
    spec_section: str
    owner_kind: OwnerKind
    owner: str
    participant: str
    no_claim: Literal["deny"]
    owner_sql: str
    write_path: WritePath
    api: RoleAccess
    worker: RoleAccess
    scheduler: RoleAccess
    observer: RoleAccess
    test_cases: tuple[RlsTestCase, ...]
    force_rls: bool = True
    enable_rls: bool = True
    client_policies: tuple[()] = ()
    cross_user_entry: str | None = None
    notes: str = ""


CLIENT_ROLES = ("anon", "authenticated")
RUNTIME_POLICY_ROLES = ("kelin_api", "kelin_worker", "kelin_scheduler", "kelin_observer")

DENY = RoleAccess()
OWNER_CRUD = RoleAccess(select=True, insert=True, update=True, delete=True)
OWNER_READ_WRITE = RoleAccess(select=True, insert=True, update=True, delete=False)
SELECT_ONLY = RoleAccess(select=True)
TRUE = "true"
UID_PRESENT = "auth.uid() IS NOT NULL"


def _uid(column: str) -> str:
    return f"{column} = auth.uid()"


def _spirit_owner(fk: str = "spirit_id") -> str:
    return f"EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = {fk} AND s.user_id = auth.uid())"


def _user_and_spirit() -> str:
    return f"{_uid('user_id')} AND {_spirit_owner('spirit_id')}"


def _pact_owner() -> str:
    return (
        "EXISTS (SELECT 1 FROM public.pacts p "
        "JOIN public.spirits s ON s.id = p.spirit_id "
        "WHERE p.id = pact_id AND s.user_id = auth.uid())"
    )


def _friend_participant() -> str:
    return (
        "EXISTS (SELECT 1 FROM public.spirits s WHERE s.user_id = auth.uid() "
        "AND s.id IN (spirit_low_id, spirit_high_id))"
    )


def _visit_party() -> str:
    return (
        "EXISTS (SELECT 1 FROM public.spirits s WHERE s.user_id = auth.uid() "
        "AND s.id IN (visitor_spirit_id, host_spirit_id))"
    )


def _postcard_receiver() -> str:
    return _spirit_owner("receiver_spirit_id")


def _owner_isolation_cases(*, writes: bool, association: bool) -> tuple[RlsTestCase, ...]:
    cases = [
        RlsTestCase("A_select_own", "A", Command.SELECT, "allow"),
        RlsTestCase("B_select_A", "B", Command.SELECT, "empty"),
        RlsTestCase("no_claim_select", "no_claim", Command.SELECT, "empty"),
        RlsTestCase("pool_A_B_none", "reuse", Command.SELECT, "empty"),
        RlsTestCase("worker_no_claim_select", "worker", Command.SELECT, "empty"),
    ]
    if writes:
        cases.extend(
            [
                RlsTestCase("A_insert_own", "A", Command.INSERT, "allow"),
                RlsTestCase("B_insert_into_A", "B", Command.INSERT, "deny"),
                RlsTestCase(
                    "forged_owner_insert",
                    "A",
                    Command.INSERT,
                    "deny",
                    "row owner/user_id is B",
                ),
                RlsTestCase("no_claim_insert", "no_claim", Command.INSERT, "deny"),
                RlsTestCase("A_update_own", "A", Command.UPDATE, "allow"),
                RlsTestCase("B_update_A", "B", Command.UPDATE, "deny"),
                RlsTestCase("A_delete_own", "A", Command.DELETE, "allow"),
                RlsTestCase("B_delete_A", "B", Command.DELETE, "deny"),
            ]
        )
    if association:
        cases = [
            *cases,
            RlsTestCase(
                "association_bypass",
                "A",
                Command.INSERT,
                "deny",
                "FK/parent owned by B",
            ),
        ]
    return tuple(cases)


def _read_only_owner_cases() -> tuple[RlsTestCase, ...]:
    return (
        RlsTestCase("A_select_own", "A", Command.SELECT, "allow"),
        RlsTestCase("B_select_A", "B", Command.SELECT, "empty"),
        RlsTestCase("no_claim_select", "no_claim", Command.SELECT, "empty"),
        RlsTestCase("A_insert_denied", "A", Command.INSERT, "deny"),
        RlsTestCase("B_update_A", "B", Command.UPDATE, "deny"),
        RlsTestCase("B_delete_A", "B", Command.DELETE, "deny"),
        RlsTestCase("pool_A_B_none", "reuse", Command.SELECT, "empty"),
    )


def _internal_worker_cases() -> tuple[RlsTestCase, ...]:
    return (
        RlsTestCase("api_select_denied", "A", Command.SELECT, "empty"),
        RlsTestCase("api_insert_denied", "A", Command.INSERT, "deny"),
        RlsTestCase("no_claim_select", "no_claim", Command.SELECT, "empty"),
        RlsTestCase("worker_select", "worker", Command.SELECT, "allow"),
        RlsTestCase("worker_insert", "worker", Command.INSERT, "allow"),
        RlsTestCase("observer_select_denied", "observer", Command.SELECT, "empty"),
        RlsTestCase("authenticated_denied", "authenticated", Command.SELECT, "empty"),
    )


WORKER_ALL = RoleAccess(
    select=True,
    insert=True,
    update=True,
    delete=True,
    using_sql=TRUE,
    with_check_sql=TRUE,
)
SCHEDULER_OUTBOX = RoleAccess(
    select=True,
    insert=True,
    using_sql=TRUE,
    with_check_sql=TRUE,
)
API_OUTBOX_INSERT = RoleAccess(
    insert=True,
    using_sql=_uid("owner_id"),
    with_check_sql=_uid("owner_id"),
)
NPC_API = RoleAccess(select=True, using_sql=UID_PRESENT)
NPC_WORKER = RoleAccess(select=True, using_sql=TRUE)
POSTCARD_API = RoleAccess(
    select=True,
    update=True,
    update_columns=("read_at",),
)
POSTCARD_WORKER = WORKER_ALL


def _t(
    table: str,
    *,
    spec_section: str,
    owner_kind: OwnerKind,
    owner: str,
    participant: str,
    owner_sql: str,
    write_path: WritePath,
    api: RoleAccess,
    worker: RoleAccess,
    test_cases: tuple[RlsTestCase, ...],
    scheduler: RoleAccess = DENY,
    observer: RoleAccess = DENY,
    cross_user_entry: str | None = None,
    notes: str = "",
) -> RlsTableExpectation:
    return RlsTableExpectation(
        table=table,
        spec_section=spec_section,
        owner_kind=owner_kind,
        owner=owner,
        participant=participant,
        no_claim="deny",
        owner_sql=owner_sql,
        write_path=write_path,
        api=api,
        worker=worker,
        scheduler=scheduler,
        observer=observer,
        test_cases=test_cases,
        cross_user_entry=cross_user_entry,
        notes=notes,
    )


RLS_MATRIX: tuple[RlsTableExpectation, ...] = (
    _t(
        "spirits",
        spec_section="7.2/6.2",
        owner_kind=OwnerKind.USER_COLUMN,
        owner="user_id = auth.uid()",
        participant="none",
        owner_sql=_uid("user_id"),
        write_path=WritePath.OWNER_DML,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=False),
    ),
    _t(
        "account_consents",
        spec_section="7.2/6.3",
        owner_kind=OwnerKind.USER_COLUMN,
        owner="user_id = auth.uid()",
        participant="none",
        owner_sql=_uid("user_id"),
        write_path=WritePath.API_OWNER_WRITE,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=False),
        notes="owner via API; no PostgREST grant",
    ),
    _t(
        "user_preferences",
        spec_section="7.2/6.3",
        owner_kind=OwnerKind.USER_COLUMN,
        owner="user_id = auth.uid()",
        participant="none",
        owner_sql=_uid("user_id"),
        write_path=WritePath.API_OWNER_WRITE,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=False),
    ),
    _t(
        "messages",
        spec_section="7.2/6.3",
        owner_kind=OwnerKind.SPIRIT_OWNER,
        owner="spirits.user_id via spirit_id",
        participant="none",
        owner_sql=_spirit_owner(),
        write_path=WritePath.OWNER_DML,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=True),
    ),
    _t(
        "conversation_windows",
        spec_section="7.2/6.3",
        owner_kind=OwnerKind.SPIRIT_OWNER,
        owner="spirits.user_id via spirit_id",
        participant="none",
        owner_sql=_spirit_owner(),
        write_path=WritePath.OWNER_DML,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=True),
    ),
    _t(
        "memories",
        spec_section="7.2/6.3",
        owner_kind=OwnerKind.SPIRIT_OWNER,
        owner="spirits.user_id via spirit_id",
        participant="none",
        owner_sql=_spirit_owner(),
        write_path=WritePath.OWNER_DML,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=True),
    ),
    _t(
        "style_samples",
        spec_section="7.2/6.3",
        owner_kind=OwnerKind.SPIRIT_OWNER,
        owner="spirits.user_id via spirit_id",
        participant="none",
        owner_sql=_spirit_owner(),
        write_path=WritePath.OWNER_DML,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=True),
        notes="spec §7.2 'styles'",
    ),
    _t(
        "feeds",
        spec_section="7.2/6.4",
        owner_kind=OwnerKind.USER_AND_SPIRIT,
        owner="user_id = auth.uid() and spirit.user_id = auth.uid()",
        participant="none",
        owner_sql=_user_and_spirit(),
        write_path=WritePath.OWNER_DML,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=True),
        notes="forged spirit_id of B with user_id of A must deny",
    ),
    _t(
        "sight_uploads",
        spec_section="7.2/6.4",
        owner_kind=OwnerKind.USER_AND_SPIRIT,
        owner="user_id = auth.uid() and spirit.user_id = auth.uid()",
        participant="none",
        owner_sql=_user_and_spirit(),
        write_path=WritePath.API_OWNER_WRITE,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=True),
        notes="client has no direct table write; API writes owner rows",
    ),
    _t(
        "growth_events",
        spec_section="7.2/6.4",
        owner_kind=OwnerKind.SPIRIT_OWNER,
        owner="spirits.user_id via spirit_id",
        participant="none",
        owner_sql=_spirit_owner(),
        write_path=WritePath.API_OWNER_WRITE,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=True),
        notes="no anon/authenticated policy; API/worker with claim",
        cross_user_entry="private.settle_spirit_state",
    ),
    _t(
        "daily_usage",
        spec_section="7.2/6.4",
        owner_kind=OwnerKind.USER_COLUMN,
        owner="user_id = auth.uid()",
        participant="none",
        owner_sql=_uid("user_id"),
        write_path=WritePath.API_OWNER_WRITE,
        api=OWNER_READ_WRITE,
        worker=OWNER_READ_WRITE,
        test_cases=(
            RlsTestCase("A_select_own", "A", Command.SELECT, "allow"),
            RlsTestCase("B_select_A", "B", Command.SELECT, "empty"),
            RlsTestCase("A_insert_own", "A", Command.INSERT, "allow"),
            RlsTestCase("B_insert_into_A", "B", Command.INSERT, "deny"),
            RlsTestCase("A_update_own", "A", Command.UPDATE, "allow"),
            RlsTestCase("B_update_A", "B", Command.UPDATE, "deny"),
            RlsTestCase("no_claim_select", "no_claim", Command.SELECT, "empty"),
            RlsTestCase("forged_owner_insert", "A", Command.INSERT, "deny", "user_id is B"),
            RlsTestCase("pool_A_B_none", "reuse", Command.SELECT, "empty"),
        ),
        notes="owner read; writes API-only (no client grant)",
    ),
    _t(
        "ai_usage",
        spec_section="7.2/6.4",
        owner_kind=OwnerKind.USER_COLUMN,
        owner="user_id = auth.uid()",
        participant="none",
        owner_sql=_uid("user_id"),
        write_path=WritePath.API_OWNER_WRITE,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=False),
        notes="no anon/authenticated policy; API/worker write",
    ),
    _t(
        "pacts",
        spec_section="7.2/6.5",
        owner_kind=OwnerKind.SPIRIT_OWNER,
        owner="spirits.user_id via spirit_id",
        participant="none",
        owner_sql=_spirit_owner(),
        write_path=WritePath.OWNER_DML,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=True),
    ),
    _t(
        "pact_sessions",
        spec_section="7.2/6.5",
        owner_kind=OwnerKind.PACT_OWNER,
        owner="pact → spirit → user",
        participant="none",
        owner_sql=_pact_owner(),
        write_path=WritePath.OWNER_DML,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=True),
        notes="spec §7.2 sessions",
        cross_user_entry="private.pact.prepare_one via owner claim",
    ),
    _t(
        "pact_mistakes",
        spec_section="7.2/6.5",
        owner_kind=OwnerKind.PACT_OWNER,
        owner="pact → spirit → user",
        participant="none",
        owner_sql=_pact_owner(),
        write_path=WritePath.OWNER_DML,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=True),
        notes="spec §7.2 mistakes",
    ),
    _t(
        "friends",
        spec_section="7.2/6.5",
        owner_kind=OwnerKind.FRIEND_PARTICIPANT,
        owner="none; both spirits are participants",
        participant="current user's spirit is spirit_low_id or spirit_high_id",
        owner_sql=_friend_participant(),
        write_path=WritePath.SECURITY_DEFINER,
        api=SELECT_ONLY,
        worker=SELECT_ONLY,
        test_cases=(
            *_read_only_owner_cases(),
            RlsTestCase(
                "add_friend_via_definer",
                "A",
                Command.INSERT,
                "allow",
                "private.add_friend_by_code; no user_id arg",
                "security_definer",
            ),
            RlsTestCase("direct_insert_denied", "A", Command.INSERT, "deny"),
        ),
        cross_user_entry="private.add_friend_by_code",
        notes="both participants may SELECT; writes are API functions only",
    ),
    _t(
        "visits",
        spec_section="7.2/6.5",
        owner_kind=OwnerKind.VISIT_PARTY,
        owner="visitor or host spirit owner",
        participant="visitor_spirit_id or host_spirit_id",
        owner_sql=_visit_party(),
        write_path=WritePath.API_OWNER_WRITE,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=True),
        cross_user_entry="private.plan_visits / private.settle_visit",
        notes="NPC visits have host_spirit_id null; visitor owner still matches",
    ),
    _t(
        "npc_profiles",
        spec_section="6.5",
        owner_kind=OwnerKind.CATALOG,
        owner="none; versioned catalog",
        participant="none",
        owner_sql=UID_PRESENT,
        write_path=WritePath.CATALOG_READ,
        api=NPC_API,
        worker=NPC_WORKER,
        test_cases=(
            RlsTestCase("api_with_claim_select", "A", Command.SELECT, "allow"),
            RlsTestCase("no_claim_select", "no_claim", Command.SELECT, "empty"),
            RlsTestCase("api_insert_denied", "A", Command.INSERT, "deny"),
            RlsTestCase("api_update_denied", "A", Command.UPDATE, "deny"),
            RlsTestCase("api_delete_denied", "A", Command.DELETE, "deny"),
            RlsTestCase("worker_select", "worker", Command.SELECT, "allow"),
            RlsTestCase("authenticated_denied", "authenticated", Command.SELECT, "empty"),
        ),
        notes="§7.2 omits this catalog; §6.5 deny client create/modify",
    ),
    _t(
        "postcards",
        spec_section="7.2/6.5",
        owner_kind=OwnerKind.POSTCARD_RECEIVER,
        owner="receiver spirit owner",
        participant="none; V0 sender has no copy",
        owner_sql=_postcard_receiver(),
        write_path=WritePath.RECEIVER_READ_WORKER_WRITE,
        api=POSTCARD_API,
        worker=POSTCARD_WORKER,
        test_cases=(
            RlsTestCase("receiver_select", "A", Command.SELECT, "allow"),
            RlsTestCase("sender_select_empty", "B", Command.SELECT, "empty", "V0 no sender copy"),
            RlsTestCase("receiver_update_read_at", "A", Command.UPDATE, "allow"),
            RlsTestCase("receiver_update_text_denied", "A", Command.UPDATE, "deny", "only read_at"),
            RlsTestCase("receiver_insert_denied", "A", Command.INSERT, "deny"),
            RlsTestCase("no_claim_select", "no_claim", Command.SELECT, "empty"),
            RlsTestCase("worker_insert", "worker", Command.INSERT, "allow"),
            RlsTestCase("pool_A_B_none", "reuse", Command.SELECT, "empty"),
        ),
        notes="receiver SELECT + read_at; remaining writes worker",
    ),
    _t(
        "reports",
        spec_section="7.2/6.5",
        owner_kind=OwnerKind.SPIRIT_OWNER,
        owner="spirits.user_id via spirit_id",
        participant="none",
        owner_sql=_spirit_owner(),
        write_path=WritePath.API_OWNER_WRITE,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=True),
        cross_user_entry="report.generate via owner claim",
    ),
    _t(
        "devices",
        spec_section="7.2/6.5",
        owner_kind=OwnerKind.USER_COLUMN,
        owner="user_id = auth.uid()",
        participant="none",
        owner_sql=_uid("user_id"),
        write_path=WritePath.API_OWNER_WRITE,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=False),
    ),
    _t(
        "notification_deliveries",
        spec_section="7.2/6.5",
        owner_kind=OwnerKind.INTERNAL_WORKER,
        owner="none for authenticated; worker-only internal table",
        participant="none",
        owner_sql=TRUE,
        write_path=WritePath.WORKER_INTERNAL,
        api=DENY,
        worker=WORKER_ALL,
        test_cases=_internal_worker_cases(),
        notes="RLS on, no authenticated policy; worker min DML",
    ),
    _t(
        "outbox_events",
        spec_section="7.2/6.5",
        owner_kind=OwnerKind.INTERNAL_OUTBOX,
        owner="owner_id snapshot for single-user jobs; null for system jobs",
        participant="none",
        owner_sql=_uid("owner_id"),
        write_path=WritePath.WORKER_INTERNAL,
        api=API_OUTBOX_INSERT,
        worker=WORKER_ALL,
        scheduler=SCHEDULER_OUTBOX,
        test_cases=(
            RlsTestCase("api_insert_own", "A", Command.INSERT, "allow"),
            RlsTestCase("api_insert_other", "A", Command.INSERT, "deny", "owner_id is B"),
            RlsTestCase("api_select_denied", "A", Command.SELECT, "empty"),
            RlsTestCase("no_claim_insert", "no_claim", Command.INSERT, "deny"),
            RlsTestCase("worker_select", "worker", Command.SELECT, "allow"),
            RlsTestCase("scheduler_insert", "scheduler", Command.INSERT, "allow"),
            RlsTestCase("observer_select_denied", "observer", Command.SELECT, "empty"),
            RlsTestCase(
                "claim_due_outbox",
                "worker",
                Command.UPDATE,
                "allow",
                "private.claim_due_outbox; no owner arg",
                "security_definer",
            ),
        ),
        cross_user_entry="private.claim_due_outbox / private.enqueue_due_state_jobs",
        notes="no anon/authenticated policy; API insert only own owner_id",
    ),
    _t(
        "idempotency_records",
        spec_section="7.2/6.5",
        owner_kind=OwnerKind.USER_COLUMN,
        owner="user_id = auth.uid()",
        participant="none",
        owner_sql=_uid("user_id"),
        write_path=WritePath.API_OWNER_WRITE,
        api=OWNER_CRUD,
        worker=OWNER_CRUD,
        test_cases=_owner_isolation_cases(writes=True, association=False),
        notes="internal table; deny client; API/worker with owner claim",
    ),
    _t(
        "account_deletions",
        spec_section="7.2/6.5",
        owner_kind=OwnerKind.INTERNAL_WORKER,
        owner="owner_id snapshot; not a live auth.users FK",
        participant="none",
        owner_sql=TRUE,
        write_path=WritePath.WORKER_INTERNAL,
        api=DENY,
        worker=WORKER_ALL,
        test_cases=(
            *_internal_worker_cases(),
            RlsTestCase(
                "mark_deleting_via_definer",
                "A",
                Command.INSERT,
                "allow",
                "private.mark_account_deleting; owner_id must equal auth.uid()",
                "security_definer",
            ),
        ),
        cross_user_entry="private.mark_account_deleting / AccountDeletionRepository.load_claimed",
        notes="API has no table policy; worker loads claimed jobs without generic owner query",
    ),
)


SPEC_7_2_GROUPS: dict[str, tuple[str, ...]] = {
    "spirits": ("spirits",),
    "account_consents": ("account_consents",),
    "user_preferences": ("user_preferences",),
    "messages/windows/memories/styles": (
        "messages",
        "conversation_windows",
        "memories",
        "style_samples",
    ),
    "feeds/sight_uploads": ("feeds", "sight_uploads"),
    "daily_usage": ("daily_usage",),
    "pacts/sessions/mistakes": ("pacts", "pact_sessions", "pact_mistakes"),
    "friends": ("friends",),
    "visits": ("visits",),
    "postcards": ("postcards",),
    "reports": ("reports",),
    "devices": ("devices",),
    "growth_events/ai_usage": ("growth_events", "ai_usage"),
    "outbox/notification/idempotency/deletions": (
        "outbox_events",
        "notification_deliveries",
        "idempotency_records",
        "account_deletions",
    ),
}


def matrix_by_table() -> dict[str, RlsTableExpectation]:
    return {row.table: row for row in RLS_MATRIX}


def _role_access(row: RlsTableExpectation, role: str) -> RoleAccess:
    return {
        "kelin_api": row.api,
        "kelin_worker": row.worker,
        "kelin_scheduler": row.scheduler,
        "kelin_observer": row.observer,
    }[role]


def _default_predicate(row: RlsTableExpectation) -> str:
    return row.owner_sql


def _access_clauses(row: RlsTableExpectation, access: RoleAccess) -> tuple[str, str]:
    default = _default_predicate(row)
    using = access.using_sql if access.using_sql is not None else default
    check = access.with_check_sql if access.with_check_sql is not None else default
    return using, check


def _access_active(access: RoleAccess) -> bool:
    return bool(access.select or access.insert or access.update or access.delete)


def enable_force_statements() -> tuple[str, ...]:
    statements: list[str] = []
    for row in RLS_MATRIX:
        if row.enable_rls:
            statements.append(f"ALTER TABLE public.{row.table} ENABLE ROW LEVEL SECURITY")
        if row.force_rls:
            statements.append(f"ALTER TABLE public.{row.table} FORCE ROW LEVEL SECURITY")
    return tuple(statements)


def policy_blueprints() -> tuple[PolicyBlueprint, ...]:
    blueprints: list[PolicyBlueprint] = []
    for row in RLS_MATRIX:
        for role in RUNTIME_POLICY_ROLES:
            access = _role_access(row, role)
            if not _access_active(access):
                continue
            using, check = _access_clauses(row, access)
            all_cmds = access.select and access.insert and access.update and access.delete
            if all_cmds and using == check:
                blueprints.append(
                    PolicyBlueprint(
                        name=f"rls_{row.table}_{role}_all",
                        table=row.table,
                        command="ALL",
                        roles=(role,),
                        using_sql=using,
                        with_check_sql=check,
                    )
                )
                continue
            if access.select:
                blueprints.append(
                    PolicyBlueprint(
                        name=f"rls_{row.table}_{role}_select",
                        table=row.table,
                        command="SELECT",
                        roles=(role,),
                        using_sql=using,
                        with_check_sql=None,
                    )
                )
            if access.insert:
                blueprints.append(
                    PolicyBlueprint(
                        name=f"rls_{row.table}_{role}_insert",
                        table=row.table,
                        command="INSERT",
                        roles=(role,),
                        using_sql=None,
                        with_check_sql=check,
                    )
                )
            if access.update:
                blueprints.append(
                    PolicyBlueprint(
                        name=f"rls_{row.table}_{role}_update",
                        table=row.table,
                        command="UPDATE",
                        roles=(role,),
                        using_sql=using,
                        with_check_sql=check,
                    )
                )
            if access.delete:
                blueprints.append(
                    PolicyBlueprint(
                        name=f"rls_{row.table}_{role}_delete",
                        table=row.table,
                        command="DELETE",
                        roles=(role,),
                        using_sql=using,
                        with_check_sql=None,
                    )
                )
    return tuple(blueprints)


def policy_create_statements() -> tuple[str, ...]:
    statements: list[str] = []
    for blueprint in policy_blueprints():
        roles = ", ".join(blueprint.roles)
        chunks = [
            f"CREATE POLICY {blueprint.name} ON public.{blueprint.table}",
            "AS PERMISSIVE",
            f"FOR {blueprint.command}",
            f"TO {roles}",
        ]
        if blueprint.using_sql is not None:
            chunks.append(f"USING ({blueprint.using_sql})")
        if blueprint.with_check_sql is not None:
            chunks.append(f"WITH CHECK ({blueprint.with_check_sql})")
        statements.append(" ".join(chunks))
    return tuple(statements)


def grant_statements() -> tuple[str, ...]:
    statements: list[str] = []
    for row in RLS_MATRIX:
        for role in RUNTIME_POLICY_ROLES:
            access = _role_access(row, role)
            table_privs: list[str] = []
            if access.select:
                table_privs.append("SELECT")
            if access.insert:
                table_privs.append("INSERT")
            if access.delete:
                table_privs.append("DELETE")
            if access.update and access.update_columns is None:
                table_privs.append("UPDATE")
            if table_privs:
                statements.append(
                    f"GRANT {', '.join(table_privs)} ON TABLE public.{row.table} TO {role}"
                )
            if access.update and access.update_columns is not None:
                columns = ", ".join(access.update_columns)
                statements.append(f"GRANT UPDATE ({columns}) ON TABLE public.{row.table} TO {role}")
    return tuple(statements)


def role_table_privileges() -> frozenset[tuple[str, str, str]]:
    granted: set[tuple[str, str, str]] = set()
    for row in RLS_MATRIX:
        for role in RUNTIME_POLICY_ROLES:
            access = _role_access(row, role)
            if access.select:
                granted.add((role, row.table, "SELECT"))
            if access.insert:
                granted.add((role, row.table, "INSERT"))
            if access.update:
                granted.add((role, row.table, "UPDATE"))
            if access.delete:
                granted.add((role, row.table, "DELETE"))
    return frozenset(granted)
