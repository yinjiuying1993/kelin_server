"""Account deletion constants. Spec §§6.5, 14.9."""

from __future__ import annotations

ACCOUNT_DELETE_OPERATION = "account.delete"
ACCOUNT_DELETE_EVENT = "account.delete"
ACCOUNT_DELETE_CONFIRM = "DELETE_MY_ACCOUNT"
ACCOUNT_DELETE_MAX_ATTEMPTS = 8
DELETION_STATUSES = frozenset(
    {
        "accepted",
        "deleting_storage",
        "deleting_auth",
        "deleting_database",
        "completed",
        "retry",
        "dead",
    }
)
ACTIVE_DELETION_STATUSES = frozenset(
    {
        "accepted",
        "deleting_storage",
        "deleting_auth",
        "deleting_database",
        "retry",
        "dead",
    }
)
STORAGE_PAGE = 20
