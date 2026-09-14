"""Prepared PostgreSQL roles. Policies and table DML grants are applied in 20260908_0006.

RLS owner/participant expectations live in `app.db.rls_matrix`.
"""

from __future__ import annotations

# Spec §4.3 / §7.1. Runtime roles must never be superuser or BYPASSRLS.
RUNTIME_ROLES = ("kelin_api", "kelin_worker", "kelin_scheduler", "kelin_observer")
MIGRATOR_ROLE = "kelin_migrator"
PREPARED_ROLES = (*RUNTIME_ROLES, MIGRATOR_ROLE)

SPEC_INDEXES_6_6 = (
    "messages_page",
    "memories_active_page",
    "windows_ready",
    "feeds_pending",
    "visits_due",
    "postcards_unread",
    "notification_due",
    "outbox_due",
    "uploads_expire",
)
