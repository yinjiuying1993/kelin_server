"""Session overlay from the user's words. Not a memory-table write."""

from __future__ import annotations

import re

from app.domain.chat.types import Correction, Overlay

_ADDRESS = re.compile(r"(?:以后叫我|叫我|我叫)([^，。！？\s]{1,12})")
_FORBIDDEN = re.compile(r"(?:别再叫|别叫我|不要叫我)([^，。！？\s]{1,12})")
_FORGET = re.compile(r"别记|不要保存|忘掉这个")
_EATS_FAKE = re.compile(r"不吃([^，。！？]{1,12})是假的")
_EATS = re.compile(r"我吃([^，。！？]{1,12})")
_AVOIDS = re.compile(r"不吃([^，。！？]{1,12})")


def apply_overlay(
    current: Overlay,
    user_text: str,
    *,
    sealed_topics: frozenset[str] = frozenset(),
    previous_user_text: str | None = None,
) -> Overlay:
    text = user_text.strip()
    reopen = "可以重新记住" in text
    address = current.address
    forbidden = list(current.forbidden_addresses)
    corrections = list(current.corrections)
    blocked = list(current.blocked_topics)
    no_comfort = current.no_comfort or "别安慰我" in text

    renamed = _ADDRESS.search(text)
    if renamed is not None:
        name = renamed.group(1)
        if _open(name, sealed_topics, reopen=reopen):
            address = name
    banned = _FORBIDDEN.search(text)
    if banned is not None:
        name = banned.group(1)
        if name not in forbidden:
            forbidden.append(name)
        if address == name:
            address = None
    if _FORGET.search(text) is not None:
        topic = (previous_user_text or "").strip() or "上一句"
        if _open(topic, sealed_topics, reopen=reopen) and topic not in blocked:
            blocked.append(topic)
    if "你记错了" in text:
        new_value = text.split("你记错了", 1)[1].lstrip("，,：: ").strip("。！？ ")
        topic, claim = _claim(new_value)
        if topic and _open(topic, sealed_topics, reopen=reopen):
            corrections = [item for item in corrections if item.topic != topic]
            corrections.append(Correction(topic=topic, claim=claim, new_value=new_value))
    return Overlay(
        address=address,
        forbidden_addresses=tuple(forbidden),
        corrections=tuple(corrections),
        blocked_topics=tuple(blocked),
        no_comfort=no_comfort,
    )


def overlay_payload(overlay: Overlay) -> dict[str, object]:
    return {
        "address": overlay.address,
        "forbidden_addresses": list(overlay.forbidden_addresses),
        "corrections": [
            {"topic": item.topic, "claim": item.claim, "new_value": item.new_value}
            for item in overlay.corrections
        ],
        "blocked_topics": list(overlay.blocked_topics),
        "no_comfort": overlay.no_comfort,
    }


def overlay_from_payload(payload: object) -> Overlay:
    if not isinstance(payload, dict):
        return Overlay()
    address = payload.get("address")
    forbidden = payload.get("forbidden_addresses")
    blocked = payload.get("blocked_topics")
    corrections = payload.get("corrections")
    parsed: list[Correction] = []
    if isinstance(corrections, list):
        for item in corrections:
            if not isinstance(item, dict):
                continue
            topic = item.get("topic")
            claim = item.get("claim")
            new_value = item.get("new_value")
            if isinstance(topic, str) and isinstance(claim, str) and isinstance(new_value, str):
                parsed.append(Correction(topic=topic, claim=claim, new_value=new_value))
    return Overlay(
        address=address if isinstance(address, str) and address else None,
        forbidden_addresses=tuple(item for item in forbidden if isinstance(item, str))
        if isinstance(forbidden, list)
        else (),
        corrections=tuple(parsed),
        blocked_topics=tuple(item for item in blocked if isinstance(item, str))
        if isinstance(blocked, list)
        else (),
        no_comfort=bool(payload.get("no_comfort")),
    )


def _open(topic: str, sealed: frozenset[str], *, reopen: bool) -> bool:
    return reopen or topic not in sealed


def _claim(new_value: str) -> tuple[str, str]:
    fake = _EATS_FAKE.search(new_value)
    if fake is not None:
        return fake.group(1), "eats"
    eats = _EATS.search(new_value)
    if eats is not None:
        return eats.group(1), "eats"
    avoids = _AVOIDS.search(new_value)
    if avoids is not None:
        return avoids.group(1), "avoids"
    return new_value[:12], "replace"
