"""Feedback submit identity. The idempotency record stores the trimmed text."""

from __future__ import annotations

FEEDBACK_OPERATION = "feedback.submit"
FEEDBACK_RESOURCE_TYPE = "feedback_submission"
FEEDBACK_RETRY_AFTER_MS = 500
FEEDBACK_TEXT_MAX = 1000
FEEDBACK_REJECTION_CODE = "MODERATION_REJECTED"
