"""Project destination, life-band hints, and connectivity. Does not settle life."""

from __future__ import annotations

from app.domain.central.types import LifeBand, SpiritStatus


def text_status(
    *,
    status: SpiritStatus,
    hunger_band: LifeBand,
    energy_band: LifeBand,
    last_error: str | None = None,
    connectivity: str | None = None,
) -> str:
    if connectivity == "offline":
        return f"{status}_offline"
    if last_error:
        return f"{status}_{last_error}"
    _ = (hunger_band, energy_band)
    return status
