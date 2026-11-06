"""Self-test model calls. They stay outside the ticket transaction."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from app.core.errors import ApiError, public_error_message
from app.domain.craft_self_test.prompt import DialogueRole, render_self_test_prompt
from app.providers.errors import ProviderError


class _SelfTestProvider(Protocol):
    async def moderate_text(self, text: str) -> str: ...

    async def complete_self_test(self, instruction: str, user_text: str) -> str: ...


class ProviderSelfTest:
    def __init__(self, provider: _SelfTestProvider) -> None:
        self._provider = provider

    async def decision(self, text: str) -> Literal["allow", "block"]:
        try:
            decision = await self._provider.moderate_text(text)
        except ProviderError as exc:
            raise _unavailable() from exc
        if decision == "block":
            return "block"
        if decision != "allow":
            raise _unavailable()
        return "allow"

    async def reply(
        self,
        *,
        snapshot_body: str,
        dialogue: Sequence[tuple[DialogueRole, str]],
        text: str,
    ) -> str:
        instruction, user_text = render_self_test_prompt(
            snapshot_body=snapshot_body,
            dialogue=dialogue,
            text=text,
        )
        try:
            return await self._provider.complete_self_test(instruction, user_text)
        except ProviderError as exc:
            raise _unavailable() from exc


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )
