"""Unique AI adapter contract. Spec §16.1.

Implementations must declare source. The P09 stub identifies as stub and must
not present itself as the live chat provider.
"""

from __future__ import annotations

from typing import Protocol

from app.providers.types import (
    ASRInput,
    AudioResult,
    ChatInput,
    ChatOutput,
    ExtractInput,
    ExtractOutput,
    SafetyInput,
    SafetyOutput,
    SearchInput,
    SearchResult,
    Transcript,
    TTSInput,
    VisionInput,
    VisionOutput,
)


class BailianProvider(Protocol):
    @property
    def source(self) -> str: ...

    async def chat(self, value: ChatInput) -> ChatOutput: ...

    async def extract(self, value: ExtractInput) -> ExtractOutput: ...

    async def transcribe(self, value: ASRInput) -> Transcript: ...

    async def synthesize(self, value: TTSInput) -> AudioResult: ...

    async def vision(self, value: VisionInput) -> VisionOutput: ...

    async def moderate(self, value: SafetyInput) -> SafetyOutput: ...

    async def search(self, value: SearchInput) -> list[SearchResult]: ...
