"""Seven-day report line generation. Spec §§8.10, 16.1–16.2.

Stub returns a public sentence. Live chat is not claimed ready. Failures stay
partial and never log prompt or memory bodies.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.domain.report import STUB_SIGNATURE_LINE
from app.providers.errors import ProviderError
from app.providers.factory import build_provider
from app.providers.protocol import BailianProvider
from app.providers.stub import ControllableProviderStub


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportLineInput(_ForbidExtra):
    title: str = Field(min_length=1, max_length=80)
    weather: str = Field(min_length=1, max_length=32)
    traits: list[str] = Field(min_length=1, max_length=2)
    marks: list[str] = Field(default_factory=list)


class ReportLineOutput(_ForbidExtra):
    text: str = Field(min_length=1, max_length=80)


class ReportLineProvider(Protocol):
    async def generate(self, value: ReportLineInput) -> ReportLineOutput: ...


class PublicReportLineProvider:
    async def generate(self, value: ReportLineInput) -> ReportLineOutput:
        del value
        return ReportLineOutput(text=STUB_SIGNATURE_LINE)


class FailingReportLineProvider:
    async def generate(self, value: ReportLineInput) -> ReportLineOutput:
        del value
        raise ProviderError("PROVIDER_TIMEOUT")


class ChatBackedReportLineProvider:
    def __init__(self, inner: BailianProvider) -> None:
        self._inner = inner

    async def generate(self, value: ReportLineInput) -> ReportLineOutput:
        del self, value
        raise ProviderError("MODEL_UNAVAILABLE")


def build_report_line_provider(settings: Settings) -> ReportLineProvider:
    inner = build_provider(settings)
    if isinstance(inner, ControllableProviderStub):
        return PublicReportLineProvider()
    return ChatBackedReportLineProvider(inner)
