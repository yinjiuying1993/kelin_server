from __future__ import annotations

import re
from typing import Any

from app.db.rls_matrix import (
    CLIENT_ROLES,
    RLS_MATRIX,
    RUNTIME_POLICY_ROLES,
    SPEC_7_2_GROUPS,
    Command,
    OwnerKind,
    enable_force_statements,
    grant_statements,
    matrix_by_table,
    policy_blueprints,
    policy_create_statements,
)
from app.models import (
    AccountConsent,
    AccountDeletion,
    AiUsage,
    ConversationWindow,
    DailyUsage,
    Device,
    Feed,
    Friend,
    GrowthEvent,
    IdempotencyRecord,
    Memory,
    Message,
    NotificationDelivery,
    NpcProfile,
    OutboxEvent,
    Pact,
    PactMistake,
    PactSession,
    Postcard,
    Report,
    SightUpload,
    Spirit,
    StyleSample,
    UserPreference,
    Visit,
)
from sqlalchemy import inspect as sa_inspect

from tests.db.schema_inventory import inventory_names

TABLE_MODELS = {
    "spirits": Spirit,
    "account_consents": AccountConsent,
    "user_preferences": UserPreference,
    "messages": Message,
    "conversation_windows": ConversationWindow,
    "memories": Memory,
    "style_samples": StyleSample,
    "feeds": Feed,
    "sight_uploads": SightUpload,
    "growth_events": GrowthEvent,
    "daily_usage": DailyUsage,
    "ai_usage": AiUsage,
    "pacts": Pact,
    "pact_sessions": PactSession,
    "pact_mistakes": PactMistake,
    "friends": Friend,
    "visits": Visit,
    "npc_profiles": NpcProfile,
    "postcards": Postcard,
    "reports": Report,
    "devices": Device,
    "notification_deliveries": NotificationDelivery,
    "outbox_events": OutboxEvent,
    "idempotency_records": IdempotencyRecord,
    "account_deletions": AccountDeletion,
}

OWNER_KIND_COLUMNS = {
    OwnerKind.USER_COLUMN: ("user_id",),
    OwnerKind.SPIRIT_OWNER: ("spirit_id",),
    OwnerKind.USER_AND_SPIRIT: ("user_id", "spirit_id"),
    OwnerKind.PACT_OWNER: ("pact_id",),
    OwnerKind.FRIEND_PARTICIPANT: ("spirit_low_id", "spirit_high_id"),
    OwnerKind.VISIT_PARTY: ("visitor_spirit_id", "host_spirit_id"),
    OwnerKind.POSTCARD_RECEIVER: ("receiver_spirit_id",),
    OwnerKind.INTERNAL_OUTBOX: ("owner_id",),
}


def _columns(model: Any) -> set[str]:
    mapper = sa_inspect(model)
    return {column.key for column in mapper.columns}


def test_owner_sql_columns_exist_on_orm() -> None:
    assert set(TABLE_MODELS) == {row.table for row in RLS_MATRIX}
    for row in RLS_MATRIX:
        present = _columns(TABLE_MODELS[row.table])
        if row.api.update_columns:
            for column in row.api.update_columns:
                assert column in present, (row.table, column)
        required = OWNER_KIND_COLUMNS.get(row.owner_kind, ())
        for column in required:
            assert column in present, (row.table, column, row.owner_kind)
        if row.owner_kind is OwnerKind.INTERNAL_WORKER:
            assert "user_id" in present or "owner_id" in present, row.table


def test_matrix_covers_every_v0_business_table() -> None:
    names = {row.table for row in RLS_MATRIX}
    assert names == inventory_names()
    assert len(RLS_MATRIX) == len(names)


def test_auth_users_is_not_in_kelin_rls_matrix() -> None:
    names = {row.table for row in RLS_MATRIX}
    assert "users" not in names
    assert "auth.users" not in names


def test_spec_7_2_groups_resolve_to_matrix_tables() -> None:
    grouped = {name for tables in SPEC_7_2_GROUPS.values() for name in tables}
    assert grouped <= {row.table for row in RLS_MATRIX}
    assert "npc_profiles" not in grouped
    assert matrix_by_table()["npc_profiles"].spec_section == "6.5"


def test_every_table_has_owner_participant_no_claim_and_test_cases() -> None:
    for row in RLS_MATRIX:
        assert row.owner.strip(), row.table
        assert row.participant.strip(), row.table
        assert row.no_claim == "deny", row.table
        assert row.enable_rls is True, row.table
        assert row.force_rls is True, row.table
        assert row.client_policies == (), row.table
        assert row.test_cases, row.table
        case_names = [case.name for case in row.test_cases]
        assert len(case_names) == len(set(case_names)), row.table
        assert any(case.command is Command.SELECT for case in row.test_cases), row.table
        assert any(case.actor in {"no_claim", "authenticated"} for case in row.test_cases), (
            row.table
        )


def test_observer_and_client_roles_have_no_policies() -> None:
    for row in RLS_MATRIX:
        assert not (row.observer.select or row.observer.insert)
        assert not (row.observer.update or row.observer.delete)
        for role in CLIENT_ROLES:
            assert role not in RUNTIME_POLICY_ROLES
    for blueprint in policy_blueprints():
        assert not set(blueprint.roles) & set(CLIENT_ROLES)
        assert "kelin_observer" not in blueprint.roles
        assert len(blueprint.name) <= 63
        assert re.fullmatch(r"rls_[a-z0-9_]+", blueprint.name)


def test_policy_blueprint_names_are_unique() -> None:
    names = [blueprint.name for blueprint in policy_blueprints()]
    assert names
    assert len(names) == len(set(names))


def test_enable_force_covers_each_public_table_once() -> None:
    statements = enable_force_statements()
    tables = [row.table for row in RLS_MATRIX]
    enables = [item for item in statements if "ENABLE ROW LEVEL SECURITY" in item]
    forces = [item for item in statements if "FORCE ROW LEVEL SECURITY" in item]
    assert len(enables) == len(tables)
    assert len(forces) == len(tables)
    for table in tables:
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in statements
        assert f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY" in statements


def test_user_scoped_predicates_use_auth_uid() -> None:
    scoped = {
        OwnerKind.USER_COLUMN,
        OwnerKind.SPIRIT_OWNER,
        OwnerKind.USER_AND_SPIRIT,
        OwnerKind.PACT_OWNER,
        OwnerKind.FRIEND_PARTICIPANT,
        OwnerKind.VISIT_PARTY,
        OwnerKind.POSTCARD_RECEIVER,
        OwnerKind.CATALOG,
        OwnerKind.INTERNAL_OUTBOX,
    }
    for row in RLS_MATRIX:
        if row.owner_kind in scoped:
            assert "auth.uid()" in row.owner_sql, row.table


def test_internal_tables_deny_api_select() -> None:
    by_table = matrix_by_table()
    assert by_table["notification_deliveries"].api.select is False
    assert by_table["account_deletions"].api.select is False
    assert by_table["outbox_events"].api.select is False
    assert by_table["outbox_events"].api.insert is True
    assert by_table["outbox_events"].scheduler.insert is True
    assert by_table["npc_profiles"].api.insert is False
    assert by_table["friends"].api.insert is False
    assert by_table["postcards"].api.insert is False
    assert by_table["postcards"].api.update_columns == ("read_at",)
    assert by_table["daily_usage"].api.delete is False


def test_true_using_only_on_internal_or_catalog_worker_paths() -> None:
    allowed_true = {
        ("npc_profiles", "kelin_worker"),
        ("notification_deliveries", "kelin_worker"),
        ("outbox_events", "kelin_worker"),
        ("outbox_events", "kelin_scheduler"),
        ("account_deletions", "kelin_worker"),
        ("postcards", "kelin_worker"),
    }
    for blueprint in policy_blueprints():
        clauses = " ".join(part for part in (blueprint.using_sql, blueprint.with_check_sql) if part)
        if clauses.strip() == "true":
            for role in blueprint.roles:
                assert (blueprint.table, role) in allowed_true, (blueprint.table, role)


def test_grant_statements_skip_observer_and_client_roles() -> None:
    grants = "\n".join(grant_statements())
    assert "kelin_observer" not in grants
    assert " TO anon" not in grants
    assert " TO authenticated" not in grants
    ddl = "\n".join(policy_create_statements())
    assert " TO anon" not in ddl
    assert " TO authenticated" not in ddl
    assert len(policy_create_statements()) == len(policy_blueprints())
