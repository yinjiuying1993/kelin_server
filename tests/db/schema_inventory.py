"""V0 table audit against server spec v2.0.0 §§6.2–6.6 and §7.2.

This module is an inventory of names, spec sections, owner paths, and
constraint/index identities. It is not a second DDL source; column types and
CHECK expressions remain in kelin_server_technical_spec.md.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SPEC = "0_kelin/2_server_pd/kelin_server_technical_spec.md"
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TableAudit:
    name: str
    spec_section: str
    owner_path: str
    constraints: tuple[str, ...]
    indexes: tuple[str, ...]
    open_questions: tuple[str, ...] = ()


SCHEMA_INVENTORY: tuple[TableAudit, ...] = (
    TableAudit(
        name="spirits",
        spec_section="6.2",
        owner_path="user_id → auth.users; RLS user_id = auth.uid()",
        constraints=(
            "pk_id",
            "uq_user_id",
            "uq_invite_code",
            "uq_user_id_client_id",
            "fk_user_id_auth_users_cascade",
            "ck_name_length",
            "ck_egg_warm_cold_wild",
            "ck_stage_whelp_formed_awake",
            "ck_status_home_away_study_lost",
            "ck_trait_ranges",
            "ck_onboarding_step_0_5",
            "ck_onboarding_complete_invariant",
        ),
        indexes=("uq_user_id", "uq_invite_code", "uq_user_id_client_id"),
    ),
    TableAudit(
        name="account_consents",
        spec_section="6.3",
        owner_path="user_id → auth.users; RLS owner",
        constraints=(
            "pk_id",
            "fk_user_id_auth_users",
            "uq_user_id_consent_type_document_version",
            "ck_consent_type",
            "ck_document_version_len",
            "ck_source_ios",
            "ck_assertion",
            "ck_assertion_matches_type",
            "ck_ai_disclosure_not_withdrawn",
        ),
        indexes=("uq_user_id_consent_type_document_version",),
        open_questions=(
            "data_notice/user_terms blocking-create vs display-only "
            "needs product/legal; columns exist in spec",
        ),
    ),
    TableAudit(
        name="user_preferences",
        spec_section="6.3",
        owner_path="user_id PK/FK auth.users; RLS owner",
        constraints=(
            "pk_user_id",
            "fk_user_id_auth_users",
            "ck_timezone_len",
            "ck_default_city_len",
            "ck_version_positive",
        ),
        indexes=("pk_user_id",),
    ),
    TableAudit(
        name="messages",
        spec_section="6.3",
        owner_path="spirit_id → spirits.user_id; RLS via spirit owner",
        constraints=(
            "pk_id",
            "fk_spirit_id_cascade",
            "fk_conversation_window_id",
            "fk_reply_to_message_id",
            "uq_spirit_id_client_id_where_client_id_not_null",
            "ck_role",
            "ck_content_length",
            "ck_source",
            "ck_status",
        ),
        indexes=(
            "messages_page",
            "uq_spirit_id_client_id_where_client_id_not_null",
        ),
    ),
    TableAudit(
        name="conversation_windows",
        spec_section="6.3",
        owner_path="spirit_id → spirits.user_id; RLS via spirit owner",
        constraints=(
            "pk_id",
            "fk_spirit_id",
            "uq_spirit_id_start_end_message",
            "uq_spirit_id_extract_client_id_where_not_null",
            "ck_status",
            "ck_extract_attempts_nonneg",
        ),
        indexes=(
            "windows_ready",
            "uq_spirit_id_start_end_message",
            "uq_spirit_id_extract_client_id_where_not_null",
        ),
    ),
    TableAudit(
        name="memories",
        spec_section="6.3",
        owner_path="spirit_id → spirits.user_id; RLS via spirit owner",
        constraints=(
            "pk_id",
            "fk_spirit_id",
            "fk_source_message_id",
            "fk_source_feed_id",
            "fk_corrected_from_id",
            "ck_type",
            "ck_summary_length",
            "ck_salience",
            "ck_confidence",
            "ck_status_active_sealed_deleted",
        ),
        indexes=("memories_active_page",),
    ),
    TableAudit(
        name="style_samples",
        spec_section="6.3",
        owner_path="spirit_id → spirits.user_id; RLS via spirit owner",
        constraints=(
            "pk_id",
            "fk_spirit_id",
            "uq_spirit_id_kind_text",
            "ck_kind",
            "ck_text_length",
        ),
        indexes=("uq_spirit_id_kind_text",),
    ),
    TableAudit(
        name="feeds",
        spec_section="6.4",
        owner_path="user_id + spirit_id; RLS owner",
        constraints=(
            "pk_id",
            "fk_user_id",
            "fk_spirit_id",
            "uq_user_id_client_id",
            "uq_spirit_id_one_active_promise",
            "ck_kind",
            "ck_status",
            "ck_promise_status",
        ),
        indexes=("feeds_pending", "uq_user_id_client_id", "uq_spirit_id_one_active_promise"),
    ),
    TableAudit(
        name="sight_uploads",
        spec_section="6.4",
        owner_path="user_id + spirit_id + feed_id; RLS owner read; client no direct write",
        constraints=(
            "pk_id",
            "fk_user_id",
            "fk_spirit_id",
            "fk_feed_id",
            "uq_user_id_client_id",
            "uq_bucket_object_path",
            "ck_status",
            "ck_expected_mime",
            "ck_expected_size",
        ),
        indexes=("uploads_expire", "uq_user_id_client_id", "uq_bucket_object_path"),
    ),
    TableAudit(
        name="growth_events",
        spec_section="6.4",
        owner_path="spirit_id; RLS enabled, deny client; API/worker only",
        constraints=(
            "pk_id",
            "fk_spirit_id",
            "uq_source_type_source_id_event_type",
            "ck_event_type_whitelist",
        ),
        indexes=("uq_source_type_source_id_event_type",),
    ),
    TableAudit(
        name="daily_usage",
        spec_section="6.4",
        owner_path="user_id; RLS owner read, API write",
        constraints=(
            "pk_user_id_usage_date_capability",
            "fk_user_id_cascade",
            "ck_capability",
            "ck_used_reserved_limit_nonneg",
        ),
        indexes=("pk_user_id_usage_date_capability",),
    ),
    TableAudit(
        name="ai_usage",
        spec_section="6.4",
        owner_path="user_id; RLS deny client; API/worker write",
        constraints=("pk_id_identity", "fk_user_id"),
        indexes=("pk_id_identity",),
    ),
    TableAudit(
        name="pacts",
        spec_section="6.5",
        owner_path="spirit_id → spirits.user_id; RLS owner",
        constraints=(
            "pk_id",
            "fk_spirit_id",
            "uq_spirit_id_one_active",
            "ck_theme",
            "ck_status",
            "ck_completeness_0_100",
        ),
        indexes=("uq_spirit_id_one_active",),
    ),
    TableAudit(
        name="pact_sessions",
        spec_section="6.5",
        owner_path="pact_id → pacts → spirit owner",
        constraints=(
            "pk_id",
            "fk_pact_id",
            "uq_pact_id_session_date",
            "uq_pact_id_client_id",
            "ck_status",
        ),
        indexes=("uq_pact_id_session_date", "uq_pact_id_client_id"),
    ),
    TableAudit(
        name="pact_mistakes",
        spec_section="6.5",
        owner_path="pact_id → pacts → spirit owner",
        constraints=("pk_id", "fk_pact_id", "fk_session_id"),
        indexes=("pk_id",),
    ),
    TableAudit(
        name="friends",
        spec_section="6.5",
        owner_path="spirit_low_id/spirit_high_id participants; API write only",
        constraints=(
            "pk_id",
            "fk_spirit_low_id",
            "fk_spirit_high_id",
            "fk_created_by_spirit_id",
            "ck_low_lt_high",
            "uq_spirit_low_id_spirit_high_id",
        ),
        indexes=("uq_spirit_low_id_spirit_high_id",),
    ),
    TableAudit(
        name="visits",
        spec_section="6.5",
        owner_path="visitor_spirit_id or host_spirit_id owner; API/worker write",
        constraints=(
            "pk_id",
            "ck_host_xor_npc",
            "uq_plan_id_destination_index",
            "uq_eligibility_key",
            "ck_status",
        ),
        indexes=("visits_due", "uq_plan_id_destination_index", "uq_eligibility_key"),
    ),
    TableAudit(
        name="npc_profiles",
        spec_section="6.5",
        owner_path="internal catalog; deny client",
        constraints=("pk_npc_id_config_version",),
        indexes=("pk_npc_id_config_version",),
    ),
    TableAudit(
        name="postcards",
        spec_section="6.5",
        owner_path="receiver_spirit_id owner; receiver may set read_at",
        constraints=(
            "pk_id",
            "fk_visit_id",
            "uq_visit_id_receiver_spirit_id",
            "ck_text_length",
        ),
        indexes=("postcards_unread", "uq_visit_id_receiver_spirit_id"),
        open_questions=(
            "sender copy visibility: spec V0 does not require returning a sender copy",
        ),
    ),
    TableAudit(
        name="reports",
        spec_section="6.5",
        owner_path="spirit_id → spirits.user_id; RLS owner",
        constraints=(
            "pk_id",
            "fk_spirit_id",
            "uq_spirit_id_report_type",
            "ck_report_type",
            "ck_status",
        ),
        indexes=("uq_spirit_id_report_type",),
    ),
    TableAudit(
        name="devices",
        spec_section="6.5",
        owner_path="user_id; RLS owner via API",
        constraints=(
            "pk_id",
            "fk_user_id",
            "uq_environment_apns_token_hash",
            "uq_user_id_installation_id_environment",
        ),
        indexes=(
            "uq_environment_apns_token_hash",
            "uq_user_id_installation_id_environment",
        ),
    ),
    TableAudit(
        name="notification_deliveries",
        spec_section="6.5",
        owner_path="internal; RLS on, no authenticated policy; worker only",
        constraints=(
            "pk_id",
            "fk_user_id",
            "fk_device_id",
            "uq_dedupe_key_device_id",
            "ck_status",
        ),
        indexes=("notification_due", "uq_dedupe_key_device_id"),
    ),
    TableAudit(
        name="outbox_events",
        spec_section="6.5",
        owner_path="owner_id snapshot for single-user jobs; internal roles only",
        constraints=("pk_id", "uq_dedupe_key", "ck_status"),
        indexes=("outbox_due", "uq_dedupe_key"),
    ),
    TableAudit(
        name="idempotency_records",
        spec_section="6.5",
        owner_path="user_id; internal, deny client",
        constraints=("pk_user_id_operation_client_id",),
        indexes=("pk_user_id_operation_client_id",),
    ),
    TableAudit(
        name="account_deletions",
        spec_section="6.5",
        owner_path="owner_id snapshot; no cascading FK to auth.users",
        constraints=("pk_id", "uq_owner_id_client_id", "ck_status"),
        indexes=("uq_owner_id_client_id",),
    ),
)


def inventory_names() -> frozenset[str]:
    return frozenset(row.name for row in SCHEMA_INVENTORY)


def discover_present_tables() -> frozenset[str]:
    """Tables that exist as ORM metadata in this repo. Spec text is not evidence."""
    try:
        importlib.import_module("app.models")
        module = importlib.import_module("app.db.base")
    except ImportError:
        return frozenset()
    base = getattr(module, "Base", None)
    metadata = getattr(base, "metadata", None)
    tables = getattr(metadata, "tables", None)
    if not isinstance(tables, dict):
        return frozenset()
    return frozenset(str(name).removeprefix("public.") for name in tables)


def missing_tables(present: Iterable[str] | None = None) -> frozenset[str]:
    implemented = frozenset(present) if present is not None else discover_present_tables()
    return inventory_names() - implemented


def alembic_configured() -> bool:
    return (REPO_ROOT / "alembic.ini").is_file()
