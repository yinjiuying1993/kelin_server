"""Craft settlement shell. Outbox consumption is not wired yet."""

from __future__ import annotations

from app.services.v3_placeholder import unavailable


def reject() -> None:
    unavailable("craft_settle")
