"""Visit postcard generation. Spec §§8.9, 16.1–16.2, 17.1.

Chat-backed live path uses empty memories. Stub/default writes public-only custom
text. Provider timeout, unavailable, or Safety reject fall back to the template.
"""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from app.core.config import Settings
from app.domain.visit_postcards import (
    PostcardSource,
    fallback_postcard_text,
    public_postcard_stub_text,
)
from app.providers.errors import ProviderCancelled, ProviderError
from app.providers.factory import build_provider
from app.providers.postcard_schema import (
    PostcardGenerateInput,
    PostcardGenerateOutput,
    PostcardPublicSubject,
    PostcardRole,
    PostcardSafetyRejected,
    assert_public_postcard_payload,
    require_postcard_output,
)
from app.providers.protocol import BailianProvider
from app.providers.types import ChatInput, SafetyInput
from app.schemas.spirit import SpiritStage


class PostcardProvider(Protocol):
    async def generate(self, value: PostcardGenerateInput) -> PostcardGenerateOutput: ...


class PublicPostcardProvider:
    async def generate(self, value: PostcardGenerateInput) -> PostcardGenerateOutput:
        payload = value.model_dump(mode="json")
        assert_public_postcard_payload(payload)
        text = public_postcard_stub_text(
            title=value.subject.title,
            weather=value.subject.weather,
            role=value.role,
        )
        return require_postcard_output({"text": text})


class FailingPostcardProvider:
    async def generate(self, value: PostcardGenerateInput) -> PostcardGenerateOutput:
        del value
        raise ProviderError("PROVIDER_TIMEOUT")


class UnsafePostcardProvider:
    async def generate(self, value: PostcardGenerateInput) -> PostcardGenerateOutput:
        del value
        return PostcardGenerateOutput(text="记得昨天的对话和坐标")


class ChatBackedPostcardProvider:
    def __init__(self, inner: BailianProvider) -> None:
        self._inner = inner

    async def generate(self, value: PostcardGenerateInput) -> PostcardGenerateOutput:
        payload = value.model_dump(mode="json")
        assert_public_postcard_payload(payload)
        raw = await self._inner.chat(
            ChatInput(
                onboarding=False,
                onboarding_step=0,
                content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                memories=[],
                style_samples=[],
            )
        )
        output = require_postcard_output({"text": raw.reply.strip()})
        body = output.text.encode("utf-8")
        safety = await self._inner.moderate(
            SafetyInput(
                sha256=hashlib.sha256(body).hexdigest(),
                size_bytes=len(body),
                body=body,
            )
        )
        if safety.decision != "allow":
            raise PostcardSafetyRejected
        return output


def build_postcard_provider(
    settings: Settings,
    *,
    inner: BailianProvider | None = None,
) -> PostcardProvider:
    adapter = inner if inner is not None else build_provider(settings)
    if adapter.source == "stub":
        return PublicPostcardProvider()
    return ChatBackedPostcardProvider(adapter)


async def resolve_postcard_text(
    provider: PostcardProvider | None,
    value: PostcardGenerateInput,
) -> tuple[str, PostcardSource]:
    fallback = fallback_postcard_text(title=value.subject.title, role=value.role)
    if provider is None:
        return fallback, "template"
    try:
        output = require_postcard_output(await provider.generate(value))
        return output.text.strip(), "provider"
    except (
        ProviderError,
        ProviderCancelled,
        PostcardSafetyRejected,
        OSError,
        TimeoutError,
        ValueError,
    ):
        return fallback, "template"


def postcard_input_from_public(
    *,
    role: PostcardRole,
    title: str,
    stage: SpiritStage,
    weather: str,
    marks: list[str],
    counterpart_title: str | None = None,
    counterpart_stage: SpiritStage | None = None,
    counterpart_weather: str | None = None,
    counterpart_marks: list[str] | None = None,
    npc_id: str | None = None,
) -> PostcardGenerateInput:
    counterpart = None
    if counterpart_title and counterpart_stage and counterpart_weather is not None:
        counterpart = PostcardPublicSubject(
            title=counterpart_title,
            stage=counterpart_stage,
            weather=counterpart_weather,
            public_marks=list(counterpart_marks or []),
        )
    return PostcardGenerateInput(
        role=role,
        subject=PostcardPublicSubject(
            title=title,
            stage=stage,
            weather=weather,
            public_marks=list(marks),
        ),
        counterpart=counterpart,
        npc_id=npc_id,
    )
