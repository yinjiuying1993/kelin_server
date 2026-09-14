"""Choose stub or the unique Bailian HTTP adapter. Spec §16.1."""

from __future__ import annotations

from app.core.config import Settings
from app.integrations.bailian import BailianHttpAdapter
from app.providers.contract import live_provider_readiness
from app.providers.protocol import BailianProvider
from app.providers.stub import ControllableProviderStub


def build_provider(settings: Settings) -> BailianProvider:
    if not live_provider_readiness(settings).chat:
        return ControllableProviderStub()
    return BailianHttpAdapter.from_settings(settings)
