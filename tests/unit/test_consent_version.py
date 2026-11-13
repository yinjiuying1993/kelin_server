"""New spirit creates require the current consent version. Replays do not."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.domain.spirit import CURRENT_CONSENT_DOCUMENT_VERSION, create_spirit_request_hash
from app.repositories.spirit import IdempotencyClaim, SpiritOwnerRow
from app.schemas.spirit import CreateSpiritRequest
from app.services.spirit import create_spirit_if_absent


def _request(version: str) -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid4()),
            "egg": "warm",
            "name": "未名",
            "consents": {
                "ai_disclosure": {
                    "document_version": version,
                    "explicitly_accepted": True,
                },
                "data_notice": {"document_version": version, "displayed": True},
                "user_terms": {"document_version": version, "displayed": True},
            },
        }
    )


async def test_new_create_rejects_an_old_document_version() -> None:
    request = _request("2026" + "-09")
    claim = IdempotencyClaim(
        inserted=True,
        request_hash=create_spirit_request_hash(request),
        status="in_progress",
        resource_id=None,
    )
    insert = AsyncMock()
    with (
        patch("app.services.spirit.spirit_repo.lock_owner_create", new=AsyncMock()),
        patch(
            "app.services.spirit.spirit_repo.fetch_spirit_for_owner",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.spirit.spirit_repo.claim_create_idempotency",
            new=AsyncMock(return_value=claim),
        ),
        patch("app.services.spirit.spirit_repo.insert_consents", new=insert),
    ):
        with pytest.raises(ApiError) as rejected:
            await create_spirit_if_absent(AsyncMock(), CurrentUser(id=uuid4()), request)
    assert rejected.value.code == "INVALID_INPUT"
    insert.assert_not_awaited()


async def test_current_version_reaches_consent_insert() -> None:
    request = _request(CURRENT_CONSENT_DOCUMENT_VERSION)
    claim = IdempotencyClaim(
        inserted=True,
        request_hash=create_spirit_request_hash(request),
        status="in_progress",
        resource_id=None,
    )

    async def _stop(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("stop")

    insert = AsyncMock(side_effect=_stop)
    with (
        patch("app.services.spirit.spirit_repo.lock_owner_create", new=AsyncMock()),
        patch(
            "app.services.spirit.spirit_repo.fetch_spirit_for_owner",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.spirit.spirit_repo.claim_create_idempotency",
            new=AsyncMock(return_value=claim),
        ),
        patch("app.services.spirit.spirit_repo.insert_consents", new=insert),
    ):
        with pytest.raises(RuntimeError, match="stop"):
            await create_spirit_if_absent(AsyncMock(), CurrentUser(id=uuid4()), request)
    kwargs = insert.await_args.kwargs
    assert kwargs["ai_version"] == CURRENT_CONSENT_DOCUMENT_VERSION
    assert kwargs["notice_version"] == CURRENT_CONSENT_DOCUMENT_VERSION
    assert kwargs["terms_version"] == CURRENT_CONSENT_DOCUMENT_VERSION


async def test_replay_of_an_old_consent_does_not_recheck_the_version() -> None:
    request = _request("2026" + "-09")
    existing = SpiritOwnerRow(id=uuid4(), client_id=request.client_id)
    claim = IdempotencyClaim(
        inserted=False,
        request_hash=create_spirit_request_hash(request),
        status="completed",
        resource_id=existing.id,
    )
    sentinel = object()
    insert = AsyncMock()
    with (
        patch("app.services.spirit.spirit_repo.lock_owner_create", new=AsyncMock()),
        patch(
            "app.services.spirit.spirit_repo.fetch_spirit_for_owner",
            new=AsyncMock(return_value=existing),
        ),
        patch(
            "app.services.spirit.spirit_repo.fetch_create_idempotency",
            new=AsyncMock(return_value=claim),
        ),
        patch("app.services.spirit.spirit_repo.insert_consents", new=insert),
        patch("app.services.spirit._require_result", new=AsyncMock(return_value=sentinel)),
    ):
        result = await create_spirit_if_absent(AsyncMock(), CurrentUser(id=uuid4()), request)
    assert result is sentinel
    insert.assert_not_awaited()
