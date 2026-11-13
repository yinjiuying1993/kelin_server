"""Feedback submit: trim, safety, idempotency, and the id-only response."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import CurrentUser, require_current_user
from app.db.rls_matrix import (
    RLS_MATRIX_FEEDBACK,
    RLS_MATRIX_V4,
    enable_force_statements,
    grant_statements,
)
from app.domain.feedback import FEEDBACK_RETRY_AFTER_MS
from app.main import create_app
from app.providers.errors import ProviderError
from app.repositories.feedback import FeedbackClaim
from app.schemas.feedback import FeedbackRequest
from app.services.feedback import submit_feedback
from fastapi.testclient import TestClient
from pydantic import ValidationError

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "20261112_0139_feedback_submissions.py"
)


class _Safety:
    def __init__(self, result: str | Exception) -> None:
        self._result = result
        self.seen: list[str] = []

    async def decision(self, text: str) -> str:
        self.seen.append(text)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _request(text: str = "想说一句") -> FeedbackRequest:
    return FeedbackRequest.model_validate({"client_id": str(uuid4()), "text": text})


def _claim(**overrides: object) -> FeedbackClaim:
    body = _request().text
    values: dict[str, object] = {
        "inserted": True,
        "request_hash": body,
        "status": "in_progress",
        "resource_id": None,
        "response_json": None,
    }
    values.update(overrides)
    return FeedbackClaim(
        inserted=bool(values["inserted"]),
        request_hash=str(values["request_hash"]),
        status=str(values["status"]),
        resource_id=values["resource_id"],  # type: ignore[arg-type]
        response_json=values["response_json"],  # type: ignore[arg-type]
    )


@asynccontextmanager
async def _transaction(*_args: object, **_kwargs: object):
    yield object()


def test_text_is_trimmed_then_counted_and_extra_fields_are_rejected() -> None:
    client_id = str(uuid4())
    trimmed = FeedbackRequest.model_validate({"client_id": client_id, "text": "  你好  "})
    assert trimmed.text == "你好"
    FeedbackRequest.model_validate({"client_id": client_id, "text": "你" * 1000})
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate({"client_id": client_id, "text": "   "})
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate({"client_id": client_id, "text": "你" * 1001})
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(
            {"client_id": client_id, "text": "你好", "contact": "person@example.com"}
        )


async def test_allow_inserts_once_and_block_stores_a_rejection() -> None:
    request = _request("  想说一句  ")
    assert request.text == "想说一句"
    feedback_id = uuid4()
    safety = _Safety("allow")
    insert = AsyncMock(return_value=feedback_id)
    complete = AsyncMock()
    reject = AsyncMock()
    with (
        patch("app.services.feedback.claimed_transaction", new=_transaction),
        patch(
            "app.services.feedback.feedback_repo.claim_feedback",
            new=AsyncMock(return_value=_claim(request_hash=request.text)),
        ),
        patch(
            "app.services.feedback.feedback_repo.lock_feedback_claim",
            new=AsyncMock(return_value=_claim(inserted=False, request_hash=request.text)),
        ),
        patch("app.services.feedback.feedback_repo.insert_feedback", new=insert),
        patch("app.services.feedback.feedback_repo.complete_feedback", new=complete),
        patch("app.services.feedback.feedback_repo.reject_feedback", new=reject),
    ):
        created = await submit_feedback(AsyncMock(), CurrentUser(id=uuid4()), request, safety)
    assert created == feedback_id
    assert safety.seen == ["想说一句"]
    insert.assert_awaited()
    complete.assert_awaited()
    reject.assert_not_awaited()
    stored_body = insert.await_args.args[3]
    assert stored_body == "想说一句"

    blocked = _Safety("block")
    insert.reset_mock()
    with (
        patch("app.services.feedback.claimed_transaction", new=_transaction),
        patch(
            "app.services.feedback.feedback_repo.claim_feedback",
            new=AsyncMock(return_value=_claim(request_hash=request.text)),
        ),
        patch("app.services.feedback.feedback_repo.insert_feedback", new=insert),
        patch("app.services.feedback.feedback_repo.reject_feedback", new=reject),
    ):
        with pytest.raises(ApiError) as rejected:
            await submit_feedback(AsyncMock(), CurrentUser(id=uuid4()), request, blocked)
    assert rejected.value.code == "MODERATION_REJECTED"
    assert rejected.value.status_code == 422
    insert.assert_not_awaited()
    reject.assert_awaited()


async def test_provider_failures_release_the_claim_and_replays_do_not_call_safety() -> None:
    request = _request()
    release = AsyncMock()
    insert = AsyncMock()
    for code, status in (("PROVIDER_TIMEOUT", 504), ("RATE_LIMITED", 429)):
        with (
            patch("app.services.feedback.claimed_transaction", new=_transaction),
            patch(
                "app.services.feedback.feedback_repo.claim_feedback",
                new=AsyncMock(return_value=_claim(request_hash=request.text)),
            ),
            patch("app.services.feedback.feedback_repo.release_feedback_claim", new=release),
            patch("app.services.feedback.feedback_repo.insert_feedback", new=insert),
        ):
            with pytest.raises(ApiError) as failed:
                await submit_feedback(
                    AsyncMock(),
                    CurrentUser(id=uuid4()),
                    request,
                    _Safety(ProviderError(code)),
                )
        assert failed.value.code == code
        assert failed.value.status_code == status
        assert failed.value.retryable is True
    insert.assert_not_awaited()
    assert release.await_count == 2

    replay_id = uuid4()
    safety = _Safety("allow")
    with (
        patch("app.services.feedback.claimed_transaction", new=_transaction),
        patch(
            "app.services.feedback.feedback_repo.claim_feedback",
            new=AsyncMock(
                return_value=_claim(
                    inserted=False,
                    request_hash=request.text,
                    status="completed",
                    resource_id=replay_id,
                )
            ),
        ),
    ):
        assert await submit_feedback(AsyncMock(), CurrentUser(id=uuid4()), request, safety) == (
            replay_id
        )
    assert safety.seen == []

    with (
        patch("app.services.feedback.claimed_transaction", new=_transaction),
        patch(
            "app.services.feedback.feedback_repo.claim_feedback",
            new=AsyncMock(
                return_value=_claim(
                    inserted=False,
                    request_hash=request.text,
                    status="completed",
                    resource_id=None,
                    response_json={"code": "MODERATION_REJECTED"},
                )
            ),
        ),
    ):
        with pytest.raises(ApiError) as replayed:
            await submit_feedback(AsyncMock(), CurrentUser(id=uuid4()), request, safety)
    assert replayed.value.code == "MODERATION_REJECTED"
    assert safety.seen == []


async def test_same_client_id_with_a_different_body_conflicts_and_in_progress_waits() -> None:
    request = _request()
    safety = _Safety("allow")
    with (
        patch("app.services.feedback.claimed_transaction", new=_transaction),
        patch(
            "app.services.feedback.feedback_repo.claim_feedback",
            new=AsyncMock(return_value=_claim(inserted=False, request_hash="另一句")),
        ),
    ):
        with pytest.raises(ApiError) as conflict:
            await submit_feedback(AsyncMock(), CurrentUser(id=uuid4()), request, safety)
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    with (
        patch("app.services.feedback.claimed_transaction", new=_transaction),
        patch(
            "app.services.feedback.feedback_repo.claim_feedback",
            new=AsyncMock(
                return_value=_claim(inserted=False, request_hash=request.text, status="in_progress")
            ),
        ),
    ):
        with pytest.raises(ApiError) as busy:
            await submit_feedback(AsyncMock(), CurrentUser(id=uuid4()), request, safety)
    assert busy.value.code == "IDEMPOTENCY_IN_PROGRESS"
    assert busy.value.retryable is True
    assert busy.value.details == {"retry_after_ms": FEEDBACK_RETRY_AFTER_MS}
    assert safety.seen == []


def test_http_hides_the_body_and_returns_only_an_id() -> None:
    application = create_app(Settings(app_env="test"))
    client = TestClient(application)
    unauthenticated = client.post(
        "/api/v1/feedback",
        json={"client_id": str(uuid4()), "text": "想说一句"},
    )
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "UNAUTHENTICATED"

    application.dependency_overrides[require_current_user] = lambda: CurrentUser(id=uuid4())
    too_long = client.post(
        "/api/v1/feedback",
        json={"client_id": str(uuid4()), "text": "测" * 1001},
    )
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "INVALID_INPUT"
    assert "测" not in too_long.text
    blank = client.post(
        "/api/v1/feedback",
        json={"client_id": str(uuid4()), "text": "   "},
    )
    assert blank.status_code == 422
    feedback_id = uuid4()
    with patch(
        "app.api.v1.feedback.submit_feedback",
        new=AsyncMock(return_value=feedback_id),
    ):
        accepted = client.post(
            "/api/v1/feedback",
            json={"client_id": str(uuid4()), "text": "想说一句"},
        )
    assert accepted.status_code == 200
    payload = accepted.json()
    assert payload["ok"] is True
    assert payload["data"] == {"id": str(feedback_id)}
    assert "patch" not in payload["data"]


def test_openapi_feedback_and_consent_examples() -> None:
    spec = create_app(Settings(app_env="test")).openapi()
    assert "/api/v1/feedback" in spec["paths"]
    schemas = spec["components"]["schemas"]
    request = schemas["FeedbackRequest"]
    accepted = schemas["FeedbackAccepted"]
    assert request["additionalProperties"] is False
    assert accepted["additionalProperties"] is False
    assert set(request["properties"]) == {"client_id", "text"}
    assert set(request["required"]) == {"client_id", "text"}
    assert set(accepted["properties"]) == {"id"}
    for name in ("AiDisclosureConsent", "DisplayedDocumentConsent"):
        version = schemas[name]["properties"]["document_version"]
        assert version["examples"] == ["2026-11-12"]


def test_migration_adds_only_the_feedback_table() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "20261112_0139"' in source
    assert 'down_revision: str | None = "20261110_0138"' in source
    assert "CREATE TABLE public.feedback_submissions" in source
    assert "ON DELETE CASCADE" in source
    assert "ALTER TABLE public.account_consents" not in source
    assert "INSERT INTO public.account_consents" not in source
    assert "enable_force_statements(RLS_MATRIX_FEEDBACK)" in source
    forced = " ".join(enable_force_statements(RLS_MATRIX_FEEDBACK))
    assert "ALTER TABLE public.feedback_submissions ENABLE ROW LEVEL SECURITY" in forced
    assert "FORCE ROW LEVEL SECURITY" in forced
    grants = " ".join(grant_statements(RLS_MATRIX_FEEDBACK))
    assert "GRANT SELECT, INSERT ON TABLE public.feedback_submissions TO kelin_api" in grants
    assert "kelin_worker" not in grants
    assert "anon" not in grants
    assert "authenticated" not in grants
    assert "feedback_submissions" not in {row.table for row in RLS_MATRIX_V4}
