from __future__ import annotations

import inspect
from uuid import uuid4

import pytest
from app.domain.spirit import (
    EGG_TRAITS,
    create_spirit_request_hash,
    traits_for_egg,
)
from app.schemas.spirit import CreateSpiritRequest
from app.services.spirit import create_spirit_if_absent
from pydantic import ValidationError


def _request(
    *,
    egg: str = "warm",
    name: str = "未名",
    client_id: object | None = None,
) -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(client_id or uuid4()),
            "egg": egg,
            "name": name,
            "consents": {
                "ai_disclosure": {
                    "document_version": "2026-09",
                    "explicitly_accepted": True,
                },
                "data_notice": {"document_version": "2026-09", "displayed": True},
                "user_terms": {"document_version": "2026-09", "displayed": True},
            },
        }
    )


def test_egg_traits_match_spec_table() -> None:
    warm = traits_for_egg("warm")
    assert (warm.closeness, warm.curiosity, warm.sharpness, warm.nocturnal, warm.stubborn) == (
        65,
        55,
        35,
        40,
        40,
    )
    cold = traits_for_egg("cold")
    assert (cold.closeness, cold.curiosity, cold.sharpness, cold.nocturnal, cold.stubborn) == (
        35,
        45,
        70,
        60,
        55,
    )
    wild = traits_for_egg("wild")
    assert (wild.closeness, wild.curiosity, wild.sharpness, wild.nocturnal, wild.stubborn) == (
        50,
        50,
        50,
        50,
        50,
    )
    assert set(EGG_TRAITS) == {"warm", "cold", "wild"}


def test_unknown_egg_has_no_traits() -> None:
    with pytest.raises(KeyError):
        traits_for_egg("hot")


def test_create_command_and_service_do_not_accept_user_id() -> None:
    payload = _request().model_dump(mode="json")
    payload["user_id"] = str(uuid4())
    with pytest.raises(ValidationError):
        CreateSpiritRequest.model_validate(payload)
    assert "user_id" not in CreateSpiritRequest.model_fields
    parameters = inspect.signature(create_spirit_if_absent).parameters
    assert "user_id" not in parameters
    assert "user" in parameters
    assert "request" in parameters


def test_payload_hash_ignores_client_id_and_changes_with_name() -> None:
    first = _request(name="未名")
    second = _request(name="未名", client_id=uuid4())
    renamed = _request(name="雾生", client_id=first.client_id)
    assert create_spirit_request_hash(first) == create_spirit_request_hash(second)
    assert create_spirit_request_hash(first) != create_spirit_request_hash(renamed)
    assert len(create_spirit_request_hash(first)) == 64
