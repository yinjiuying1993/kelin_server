"""Seven-day report rules, eligibility constants, and stable hashes. Spec §§8.10, 9.1, 14.7–14.8."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from uuid import UUID

from app.domain.bootstrap import REPORT_UNLOCK_AFTER, REQUIRED_DIALOGUE_ROUNDS
from app.domain.room import STATUS_LAYER

REPORT_TYPE = "seven_day"
REPORT_RULES_VERSION = "report-rules-v1"
REPORT_PROMPT_VERSION = "report_line/v1"
REPORT_GENERATE_EVENT = "report.generate"
REPORT_AGGREGATE_TYPE = "report"
REPORT_LINE_OPERATION = "report.line"
LINE_MAX_ATTEMPTS = 3
TOP_MEMORY_LIMIT = 3
TOP_TRAIT_LIMIT = 2
TRAIT_HIGH_THRESHOLD = 50
TRAIT_DIMENSIONS: tuple[str, ...] = (
    "closeness",
    "curiosity",
    "sharpness",
    "nocturnal",
    "stubborn",
)
FALLBACK_TITLE = "还没长定的灵"
SCHOLAR_TITLE_SUFFIX = " · 学者"
STUB_SIGNATURE_LINE = "它最像你的地方，是安静里仍肯靠近。"

_TITLE_RULES: tuple[tuple[bool, bool, bool, str], ...] = (
    (True, True, False, "夜行毒舌"),
    (True, False, True, "会翻旧账的暖炉"),
    (False, True, True, "阴天收集者"),
    (False, False, True, "温石头"),
    (True, True, True, "守夜的损友"),
    (False, True, False, "雾里旁观"),
    (True, False, False, "白日锋利"),
)


def report_generate_dedupe_key(report_id: UUID) -> str:
    return f"report-generate:{report_id}"


def trait_is_high(value: int) -> bool:
    return int(value) >= TRAIT_HIGH_THRESHOLD


def title_from_traits(
    *,
    closeness: int,
    sharpness: int,
    nocturnal: int,
    scholar_marks: Sequence[str],
) -> str:
    sharpness_high = trait_is_high(sharpness)
    nocturnal_high = trait_is_high(nocturnal)
    closeness_high = trait_is_high(closeness)
    title = FALLBACK_TITLE
    for sharp, night, close, label in _TITLE_RULES:
        if (
            sharpness_high is sharp
            and nocturnal_high is night
            and closeness_high is close
        ):
            title = label
            break
    if scholar_marks:
        return f"{title}{SCHOLAR_TITLE_SUFFIX}"
    return title


def top_traits_from_values(values: Mapping[str, int]) -> tuple[dict[str, int | str], ...]:
    ranked = sorted(
        (
            (str(dimension), int(values.get(dimension, 0)))
            for dimension in TRAIT_DIMENSIONS
        ),
        key=lambda item: (-item[1], TRAIT_DIMENSIONS.index(item[0])),
    )
    return tuple(
        {"dimension": dimension, "value": value}
        for dimension, value in ranked[:TOP_TRAIT_LIMIT]
    )


def public_layers_for_report(*, status: str, scholar_marks: Sequence[str]) -> tuple[str, ...]:
    layer = STATUS_LAYER[status] if status in STATUS_LAYER else "spirit"
    layers = [layer]
    if scholar_marks:
        layers.append("scholar")
    return tuple(layers)


def report_line_request_hash(*, report_id: UUID, expected_version: int) -> str:
    canonical = json.dumps(
        {"expected_version": expected_version, "report_id": str(report_id)},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stable_card_hash(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stable_card_fields(card: Mapping[str, object]) -> dict[str, object]:
    return {
        key: card[key]
        for key in (
            "id",
            "invite_code",
            "rules_version",
            "scholar_marks",
            "spirit",
            "title",
            "top_memories",
            "top_traits",
            "room_weather",
        )
        if key in card
    }


def has_stable_card(
    *,
    title: str | None,
    room_weather: str | None,
    invite_code: str | None,
    spirit_snapshot: Mapping[str, object] | None,
) -> bool:
    if not title or not room_weather or not invite_code:
        return False
    if not isinstance(spirit_snapshot, Mapping):
        return False
    return bool(spirit_snapshot.get("id") and spirit_snapshot.get("name"))


__all__ = [
    "FALLBACK_TITLE",
    "LINE_MAX_ATTEMPTS",
    "REPORT_AGGREGATE_TYPE",
    "REPORT_GENERATE_EVENT",
    "REPORT_LINE_OPERATION",
    "REPORT_PROMPT_VERSION",
    "REPORT_RULES_VERSION",
    "REPORT_TYPE",
    "REPORT_UNLOCK_AFTER",
    "REQUIRED_DIALOGUE_ROUNDS",
    "STUB_SIGNATURE_LINE",
    "TOP_MEMORY_LIMIT",
    "TOP_TRAIT_LIMIT",
    "TRAIT_DIMENSIONS",
    "TRAIT_HIGH_THRESHOLD",
    "has_stable_card",
    "public_layers_for_report",
    "report_generate_dedupe_key",
    "report_line_request_hash",
    "stable_card_fields",
    "stable_card_hash",
    "title_from_traits",
    "top_traits_from_values",
    "trait_is_high",
]
