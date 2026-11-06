"""lost / food-recall pure rules. No IO."""

from __future__ import annotations

from app.domain.awareness.types import SpiritStatus


def food_recall_returns_home(status: SpiritStatus, *, food_success: bool) -> bool:
    return status == "lost" and food_success
