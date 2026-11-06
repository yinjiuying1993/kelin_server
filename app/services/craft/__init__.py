"""Craft services. HTTP handlers stay at 501. Index writes go through OwnedMatchIndex."""

from __future__ import annotations

from typing import NoReturn

from app.services.craft.create import create_craft_skill
from app.services.craft.edit import delete_craft_skill, save_craft_skill, set_craft_visibility
from app.services.craft.owner_list import get_owner_skill, list_owner_skills
from app.services.v3_placeholder import unavailable

__all__ = [
    "create_craft_skill",
    "delete_craft_skill",
    "get_owner_skill",
    "list_owner_skills",
    "reject",
    "save_craft_skill",
    "set_craft_visibility",
]


def reject() -> NoReturn:
    unavailable("craft")
