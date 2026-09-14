from __future__ import annotations

from typing import Any

STATUS_ERROR_CODES: dict[int, str] = {
    400: "DOMAIN_INVALID",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    410: "UPLOAD_EXPIRED",
    422: "INVALID_INPUT",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "DEPENDENCY_UNAVAILABLE",
    504: "PROVIDER_TIMEOUT",
}

KNOWN_ERROR_CODES = frozenset(
    {
        *STATUS_ERROR_CODES.values(),
        "IDEMPOTENCY_IN_PROGRESS",
        "IDEMPOTENCY_CONFLICT",
        "PACT_ALREADY_ACTIVE",
        "QUOTA_EXCEEDED",
        "MODEL_UNAVAILABLE",
        "ONBOARDING_INCOMPLETE",
        "ONBOARDING_ALREADY_COMPLETED",
        "MODERATION_REJECTED",
        "UPLOAD_NOT_READY",
        "INVALID_CURSOR",
        "MEMORY_NOT_ACTIVE",
        "SPIRIT_NOT_LOST",
        "RECALL_SOURCE_INVALID",
        "PACT_DAY_CLOSED",
        "PACT_SESSION_ALREADY_SUBMITTED",
        "FRIEND_NOT_ALLOWED",
        "SELF_FRIEND_NOT_ALLOWED",
        "REPORT_NOT_ELIGIBLE",
        "REPORT_LINE_UNAVAILABLE",
        "ACCOUNT_DELETE_PENDING",
        "PACT_QUESTION_ALREADY_ANSWERED",
        "PACT_QUESTION_NOT_FOUND",
        "PROMISE_NOT_ACTIVE",
        "ASR_EMPTY_RESULT",
    }
)


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details


def public_error_code(status_code: int, detail: object) -> str:
    if isinstance(detail, str) and detail in KNOWN_ERROR_CODES:
        return detail
    return STATUS_ERROR_CODES.get(status_code, "INTERNAL_ERROR")


def public_error_message(code: str) -> str:
    return {
        "UNAUTHENTICATED": "unauthenticated",
        "INVALID_INPUT": "invalid input",
        "INVALID_CURSOR": "invalid cursor",
        "NOT_FOUND": "not found",
        "INTERNAL_ERROR": "internal error",
        "FORBIDDEN": "forbidden",
        "CONFLICT": "conflict",
        "DEPENDENCY_UNAVAILABLE": "dependency unavailable",
        "MODEL_UNAVAILABLE": "model unavailable",
        "PROVIDER_TIMEOUT": "provider timeout",
        "QUOTA_EXCEEDED": "quota exceeded",
        "RATE_LIMITED": "rate limited",
        "PROMISE_NOT_ACTIVE": "promise not active",
        "UPLOAD_EXPIRED": "upload expired",
        "UPLOAD_NOT_READY": "upload not ready",
        "MODERATION_REJECTED": "moderation rejected",
        "ASR_EMPTY_RESULT": "empty transcription",
        "PACT_ALREADY_ACTIVE": "an active pact already exists",
        "PACT_DAY_CLOSED": "pact day is closed",
        "PACT_SESSION_ALREADY_SUBMITTED": "pact session already submitted",
        "PACT_QUESTION_ALREADY_ANSWERED": "pact question already answered",
        "PACT_QUESTION_NOT_FOUND": "pact question not found",
        "SELF_FRIEND_NOT_ALLOWED": "cannot add yourself as a friend",
        "FRIEND_NOT_ALLOWED": "friend is not allowed",
        "REPORT_NOT_ELIGIBLE": "report not eligible",
        "REPORT_LINE_UNAVAILABLE": "report line unavailable",
        "ACCOUNT_DELETE_PENDING": "account deletion is pending",
        "SPIRIT_NOT_LOST": "spirit is not lost",
        "RECALL_SOURCE_INVALID": "recall source is invalid",
    }.get(code, "request failed")
