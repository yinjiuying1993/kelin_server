from pathlib import Path

from tests.db.schema_inventory import (
    SCHEMA_INVENTORY,
    SPEC,
    TableAudit,
    alembic_configured,
    discover_present_tables,
    inventory_names,
    missing_tables,
)

# Headings under spec §§6.2–6.5. Keep in lockstep with kelin_server_technical_spec.md.
CANONICAL_V0_TABLES = frozenset(
    {
        "spirits",
        "account_consents",
        "user_preferences",
        "messages",
        "conversation_windows",
        "memories",
        "style_samples",
        "feeds",
        "sight_uploads",
        "growth_events",
        "daily_usage",
        "ai_usage",
        "pacts",
        "pact_sessions",
        "pact_mistakes",
        "friends",
        "visits",
        "npc_profiles",
        "postcards",
        "reports",
        "devices",
        "notification_deliveries",
        "outbox_events",
        "idempotency_records",
        "account_deletions",
    }
)

SPEC_INDEXES_6_6 = frozenset(
    {
        "messages_page",
        "memories_active_page",
        "windows_ready",
        "feeds_pending",
        "visits_due",
        "postcards_unread",
        "notification_due",
        "outbox_due",
        "uploads_expire",
    }
)


def test_inventory_covers_all_spec_v0_tables() -> None:
    assert inventory_names() == CANONICAL_V0_TABLES
    assert len(SCHEMA_INVENTORY) == len(CANONICAL_V0_TABLES)


def test_inventory_table_names_are_unique() -> None:
    names = [row.name for row in SCHEMA_INVENTORY]
    assert len(names) == len(set(names))


def test_every_table_has_owner_constraints_indexes_and_spec_section() -> None:
    for row in SCHEMA_INVENTORY:
        assert row.spec_section.startswith("6."), row.name
        assert row.owner_path.strip(), row.name
        assert row.constraints, row.name
        assert row.indexes, row.name


def test_spec_pointer_names_server_v2_document() -> None:
    assert SPEC.endswith("kelin_server_technical_spec.md")


def test_spec_section_6_6_indexes_are_attached_to_tables() -> None:
    listed = {name for row in SCHEMA_INVENTORY for name in row.indexes}
    assert SPEC_INDEXES_6_6 <= listed


def test_open_questions_do_not_drop_required_tables() -> None:
    flagged = {row.name for row in SCHEMA_INVENTORY if row.open_questions}
    assert flagged <= {"account_consents", "postcards"}
    assert "account_consents" in inventory_names()
    assert "postcards" in inventory_names()


def test_orm_has_identity_and_engagement_tables() -> None:
    repo = Path(__file__).resolve().parents[2]
    assert alembic_configured()
    assert (repo / "app" / "db" / "migrations" / "versions").is_dir()
    present = discover_present_tables()
    implemented = CANONICAL_V0_TABLES
    assert implemented <= present
    assert missing_tables() == frozenset()


def test_external_auth_users_is_not_a_kelin_migration_table() -> None:
    assert "users" not in inventory_names()
    assert "auth.users" not in inventory_names()


def test_inventory_rows_are_frozen() -> None:
    assert all(isinstance(row, TableAudit) for row in SCHEMA_INVENTORY)
