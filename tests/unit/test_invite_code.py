from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import StringIO
from unittest.mock import patch

import pytest
from app.core.config import Settings
from app.core.logging import configure_logging
from app.domain.invite_code import (
    INVITE_CODE_ALPHABET,
    INVITE_CODE_LENGTH,
    generate_invite_code,
    invite_code_is_valid,
)
from app.schemas.spirit import CreateSpiritRequest
from app.services.invite_code import (
    InviteCodeAllocationError,
    allocate_invite_code,
    is_invite_code_unique_conflict,
)
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError


class _UniqueInvite(Exception):
    sqlstate = "23505"
    constraint_name = "spirits_invite_code_key"


class _UniqueUser(Exception):
    sqlstate = "23505"
    constraint_name = "spirits_user_id_key"


class _FakeSession:
    @asynccontextmanager
    async def begin_nested(self) -> AsyncIterator[None]:
        yield


def _invite_conflict() -> IntegrityError:
    return IntegrityError("INSERT", {}, _UniqueInvite("dup"))


def _user_conflict() -> IntegrityError:
    return IntegrityError("INSERT", {}, _UniqueUser("dup"))


def test_alphabet_is_eight_unambiguous_chars() -> None:
    assert INVITE_CODE_LENGTH == 8
    assert len(INVITE_CODE_ALPHABET) == 32
    assert len(set(INVITE_CODE_ALPHABET)) == 32
    assert set("0O1I").isdisjoint(INVITE_CODE_ALPHABET)
    assert INVITE_CODE_ALPHABET == "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def test_generate_uses_secrets_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_choice(seq: str) -> str:
        seen.append(seq)
        return "A"

    monkeypatch.setattr("app.domain.invite_code.secrets.choice", fake_choice)
    assert generate_invite_code() == "A" * INVITE_CODE_LENGTH
    assert seen == [INVITE_CODE_ALPHABET] * INVITE_CODE_LENGTH


def test_batch_generate_codes_are_valid_and_unique() -> None:
    codes = [generate_invite_code() for _ in range(256)]
    assert len(set(codes)) == len(codes)
    for code in codes:
        assert len(code) == INVITE_CODE_LENGTH
        assert invite_code_is_valid(code)
        assert set(code) <= set(INVITE_CODE_ALPHABET)
        assert set("0O1I").isdisjoint(code)


@pytest.mark.parametrize(
    "code",
    ["ABCD2340", "ABCD234O", "ABCD2341", "ABCD234I", "abcd2345", "ABCD234", "ABCD23456"],
)
def test_invalid_codes_are_rejected(code: str) -> None:
    assert invite_code_is_valid(code) is False


def test_create_spirit_request_rejects_client_invite_code() -> None:
    payload = {
        "client_id": "11111111-1111-1111-1111-111111111111",
        "egg": "warm",
        "name": "未名",
        "invite_code": "ABCD2345",
        "consents": {
            "ai_disclosure": {"document_version": "2026-09", "explicitly_accepted": True},
            "data_notice": {"document_version": "2026-09", "displayed": True},
            "user_terms": {"document_version": "2026-09", "displayed": True},
        },
    }
    with pytest.raises(ValidationError):
        CreateSpiritRequest.model_validate(payload)


def test_unique_invite_conflict_is_detected() -> None:
    assert is_invite_code_unique_conflict(_invite_conflict()) is True
    assert is_invite_code_unique_conflict(_user_conflict()) is False


def test_allocate_retries_unique_invite_conflict() -> None:
    codes = iter(["ABCD2345", "EFGH6789"])
    reserved: list[str] = []

    async def reserve(code: str) -> None:
        reserved.append(code)
        if code == "ABCD2345":
            raise _invite_conflict()

    allocated = asyncio.run(
        allocate_invite_code(_FakeSession(), reserve, generate=lambda: next(codes))
    )
    assert allocated == "EFGH6789"
    assert reserved == ["ABCD2345", "EFGH6789"]


def test_allocate_does_not_retry_other_unique_conflicts() -> None:
    async def reserve(_code: str) -> None:
        raise _user_conflict()

    with pytest.raises(IntegrityError):
        asyncio.run(allocate_invite_code(_FakeSession(), reserve, generate=lambda: "ABCD2345"))


def test_allocate_exhaustion_does_not_include_code() -> None:
    async def reserve(_code: str) -> None:
        raise _invite_conflict()

    buf = StringIO()
    with patch("sys.stdout", buf):
        configure_logging(Settings(app_env="test"))
        with pytest.raises(InviteCodeAllocationError, match="exhausted") as caught:
            asyncio.run(
                allocate_invite_code(
                    _FakeSession(),
                    reserve,
                    generate=lambda: "ABCD2345",
                    max_attempts=3,
                )
            )
    assert "ABCD2345" not in str(caught.value)
    assert "ABCD2345" not in buf.getvalue()
