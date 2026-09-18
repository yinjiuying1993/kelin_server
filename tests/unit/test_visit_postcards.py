"""P16-T06 both-receiver postcards: public input, Provider fallback, uniqueness."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from app.contracts.openapi import export_openapi
from app.core.config import Settings
from app.domain.visit_postcards import fallback_postcard_text, public_postcard_stub_text
from app.domain.visits import visit_template_text
from app.main import create_app
from app.providers.postcard import (
    ChatBackedPostcardProvider,
    FailingPostcardProvider,
    PublicPostcardProvider,
    UnsafePostcardProvider,
    build_postcard_provider,
    postcard_input_from_public,
    resolve_postcard_text,
)
from app.providers.postcard_schema import (
    PostcardGenerateInput,
    PostcardPublicSubject,
    PostcardRole,
)
from app.providers.types import ChatInput, ChatOutput, SafetyInput, SafetyOutput
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "app" / "db" / "migrations" / "versions" / "20260908_0016_visit_postcard_provider.py"
PROVIDER = ROOT / "app" / "providers" / "postcard.py"
SERVICE = ROOT / "app" / "services" / "visit_settle.py"
_OPENAPI_SHA = "98d1c74287949966df83779e5188f90eb408ec50bef6ee472ea707472fbdc0af"


def _input(*, role: PostcardRole = "visitor") -> PostcardGenerateInput:
    return postcard_input_from_public(
        role=role,
        title="甲居",
        stage="whelp",
        weather="cloudy",
        marks=["interview-v1"],
        counterpart_title="串门客",
        counterpart_stage="whelp",
        counterpart_weather="cloudy",
        counterpart_marks=[],
    )


def test_postcard_input_rejects_private_fields_and_keeps_openapi() -> None:
    with pytest.raises(ValidationError):
        PostcardGenerateInput.model_validate(
            {
                "role": "visitor",
                "subject": {
                    "title": "甲居",
                    "stage": "whelp",
                    "weather": "cloudy",
                    "public_marks": [],
                    "user_id": str(uuid4()),
                },
            }
        )
    with pytest.raises(ValidationError):
        PostcardPublicSubject.model_validate(
            {
                "title": "甲居",
                "stage": "whelp",
                "weather": "cloudy",
                "public_marks": [],
                "memory": "昨天聊过",
            }
        )
    dumped = _input().model_dump(mode="json")
    blob = json.dumps(dumped, ensure_ascii=False)
    assert "user_id" not in blob
    assert "记忆" not in blob
    assert "conversation" not in blob
    assert set(dumped["subject"]) == {"title", "stage", "weather", "public_marks"}
    exported = export_openapi(create_app(Settings(app_env="test")))
    assert exported.sha256 == _OPENAPI_SHA


def test_catalog_has_public_input_and_no_api_grant() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "private.visit_postcard_public_input(p_visit_id uuid)" in sql
    assert "GRANT EXECUTE ON FUNCTION private.visit_postcard_public_input(uuid) TO kelin_worker" in sql
    assert "visit_postcard_public_input(uuid) TO kelin_api" not in sql
    assert "GRANT EXECUTE ON FUNCTION private.settle_visit(uuid, timestamptz, text, text) " in sql
    assert "settle_visit(uuid, timestamptz, text, text) TO kelin_api" not in sql
    assert "p_user_id" not in sql
    assert "get_logger" not in PROVIDER.read_text(encoding="utf-8")
    assert "get_logger" not in SERVICE.read_text(encoding="utf-8")
    assert "datetime.now()" not in SERVICE.read_text(encoding="utf-8")
    assert "host_spirit_id" not in SERVICE.read_text(encoding="utf-8")


def test_provider_custom_text_timeout_and_safety_fallback() -> None:
    custom = asyncio.run(PublicPostcardProvider().generate(_input()))
    assert "甲居" in custom.text
    assert "cloudy" in custom.text
    assert "user_id" not in custom.text
    timeout, timeout_src = asyncio.run(resolve_postcard_text(FailingPostcardProvider(), _input()))
    assert timeout_src == "template"
    assert timeout == visit_template_text(title="甲居", for_host=False)
    unsafe, unsafe_src = asyncio.run(resolve_postcard_text(UnsafePostcardProvider(), _input()))
    assert unsafe_src == "template"
    assert unsafe == fallback_postcard_text(title="甲居", role="visitor")
    host, host_src = asyncio.run(resolve_postcard_text(PublicPostcardProvider(), _input(role="host")))
    assert host_src == "provider"
    assert host == public_postcard_stub_text(title="甲居", weather="cloudy", role="host")
    assert host != timeout


def test_chat_backed_uses_empty_memories_and_safety_block() -> None:
    asyncio.run(_assert_chat_backed())


async def _assert_chat_backed() -> None:
    captured: list[ChatInput] = []

    class _Inner:
        source = "provider"
        decision = "allow"

        async def chat(self, value: ChatInput) -> ChatOutput:
            captured.append(value)
            payload = json.loads(value.content)
            return ChatOutput(reply=f"路过{payload['subject']['title']}。")

        async def moderate(self, value: SafetyInput) -> SafetyOutput:
            del value
            return SafetyOutput(decision=self.decision)

    inner = _Inner()
    provider = ChatBackedPostcardProvider(inner)  # type: ignore[arg-type]
    text, source = await resolve_postcard_text(provider, _input())
    assert source == "provider"
    assert text == "路过甲居。"
    assert captured[0].memories == []
    assert captured[0].style_samples == []
    assert "user_id" not in captured[0].content
    inner.decision = "block"
    _blocked, blocked_src = await resolve_postcard_text(provider, _input())
    assert blocked_src == "template"
    stub = build_postcard_provider(Settings(app_env="test"))
    assert isinstance(stub, PublicPostcardProvider)
