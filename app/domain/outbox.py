"""Generic outbox claim, lease, and fencing keys. Spec §§17.1–17.2."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.domain.spirit_state import require_aware

DEFAULT_OUTBOX_LEASE_SECONDS = 120
DEFAULT_OUTBOX_BATCH = 20
MAX_OUTBOX_BATCH = 50
MAX_RETRY_BACKOFF_SECONDS = 300
NOTIFICATION_PLAN_EVENT = "notification.plan_user"
PUSH_DISPATCH_EVENT = "push.dispatch"


def lease_expires_at(now: datetime, lease_seconds: int = DEFAULT_OUTBOX_LEASE_SECONDS) -> datetime:
    require_aware(now, field="now")
    seconds = max(int(lease_seconds), 1)
    return now + timedelta(seconds=seconds)


def retry_backoff_seconds(attempt_count: int) -> int:
    attempt = max(int(attempt_count), 1)
    return min(MAX_RETRY_BACKOFF_SECONDS, 2**attempt)
