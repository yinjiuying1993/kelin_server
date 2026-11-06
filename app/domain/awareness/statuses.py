"""home/study/away/lost helpers. No IO."""

from __future__ import annotations

from app.domain.awareness.types import SpiritStatus

STATUS_PRIORITY: tuple[SpiritStatus, ...] = ("lost", "away", "home")


def without_study(status: SpiritStatus) -> SpiritStatus:
    """求知不再作为去向。残留的 study 对外视为串门。"""
    if status == "study":
        return "away"
    if status == "lost":
        return "home"
    return status


def letter_key_for(status: SpiritStatus) -> str | None:
    shown = without_study(status)
    if shown == "home":
        return None
    return shown
