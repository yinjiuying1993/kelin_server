"""Awareness module. Central calls this; it must not import other module services."""

from __future__ import annotations

from app.domain.central.types import LifeBand, SpiritStatus
from app.domain.central.views import AwarenessView
from app.services.awareness.settle_service import AwarenessSettleResult, settle_now

__all__ = ["AwarenessSettleResult", "settle", "settle_now"]


def settle(
    *,
    status: SpiritStatus,
    hunger_band: LifeBand,
    energy_band: LifeBand,
    nocturnal_band: str | None = None,
    letter_candidates: tuple[str, ...] = (),
) -> AwarenessView:
    """Read-only View for other modules. Persistence is settle_now."""
    del nocturnal_band
    return AwarenessView(
        status=status,
        hunger_band=hunger_band,
        energy_band=energy_band,
        letter_candidates=letter_candidates,
    )
