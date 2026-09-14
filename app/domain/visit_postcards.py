"""Receiver-customized visit postcards. Spec §§8.9, 14.4, 16.2.

Provider input is public title/stage/weather/marks only. Failed or unsafe model
output falls back to the fixed template and must not block settle or home.
"""

from __future__ import annotations

from typing import Literal

from app.domain.visits import visit_template_text

POSTCARD_TEXT_MIN = 1
POSTCARD_TEXT_MAX = 300
PostcardRole = Literal["visitor", "host"]
PostcardSource = Literal["provider", "template"]

PRIVATE_POSTCARD_MARKERS: frozenset[str] = frozenset(
    {
        "user_id",
        "email",
        "avatar",
        "photo",
        "image",
        "latitude",
        "longitude",
        "location",
        "memory",
        "memories",
        "conversation",
        "prompt",
        "hunger",
        "energy",
        "mood",
        "bond",
        "closeness",
        "curiosity",
        "sharpness",
        "nocturnal",
        "stubborn",
        "记忆",
        "对话",
        "坐标",
        "照片",
    }
)


def postcard_text_is_safe(text: str) -> bool:
    stripped = text.strip()
    if not (POSTCARD_TEXT_MIN <= len(stripped) <= POSTCARD_TEXT_MAX):
        return False
    lowered = stripped.lower()
    return not any(marker in stripped or marker in lowered for marker in PRIVATE_POSTCARD_MARKERS)


def public_postcard_stub_text(*, title: str, weather: str, role: PostcardRole) -> str:
    cleaned = title.strip() or "未名"
    sky = weather.strip() or "cloudy"
    if role == "host":
        return f"{cleaned}来过。"
    return f"去过{cleaned}，{sky}下带回一张字条。"


def fallback_postcard_text(*, title: str, role: PostcardRole) -> str:
    return visit_template_text(title=title, for_host=role == "host")
