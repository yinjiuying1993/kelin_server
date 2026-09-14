"""Provider errors. Stub failures must not be stored as a spirit reply."""

from __future__ import annotations


class ProviderError(Exception):
    """Temporary upstream failure mapped to MODEL_UNAVAILABLE."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ProviderCancelled(Exception):
    """Stub or caller cancelled before a reply existed."""


class ProviderStubUnsupported(ProviderError):
    """This stub does not implement the capability. It is not a live provider."""

    def __init__(self, capability: str) -> None:
        self.capability = capability
        super().__init__("MODEL_UNAVAILABLE")


class ProviderCapabilityUnwired(ProviderError):
    """Live adapter has no HTTP path for this capability yet."""

    def __init__(self, capability: str) -> None:
        self.capability = capability
        super().__init__("MODEL_UNAVAILABLE")
