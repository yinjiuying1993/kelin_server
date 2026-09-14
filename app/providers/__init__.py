"""In-process AI provider adapters and the factory that selects stub or live HTTP."""

from app.providers.contract import (
    PROVIDER_TIMEOUT_SECONDS,
    live_provider_readiness,
    map_provider_failure,
)
from app.providers.errors import ProviderCancelled, ProviderError, ProviderStubUnsupported
from app.providers.protocol import BailianProvider
from app.providers.stub import STUB_LABEL, STUB_SOURCE, ControllableProviderStub
from app.providers.types import (
    ChatInput,
    ChatOutput,
    ExtractInput,
    ExtractOutput,
    ProviderCallRecord,
    SearchInput,
    SearchResult,
    StubStep,
)

__all__ = [
    "PROVIDER_TIMEOUT_SECONDS",
    "STUB_LABEL",
    "STUB_SOURCE",
    "BailianProvider",
    "ChatInput",
    "ChatOutput",
    "ControllableProviderStub",
    "ExtractInput",
    "ExtractOutput",
    "ProviderCallRecord",
    "ProviderCancelled",
    "ProviderError",
    "ProviderStubUnsupported",
    "SearchInput",
    "SearchResult",
    "StubStep",
    "live_provider_readiness",
    "map_provider_failure",
]
