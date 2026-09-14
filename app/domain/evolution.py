"""Formation, awakening, and scholar-mark rules. Spec §8.5; P13-T03.

Stages are unidirectional: whelp → formed → awake. Sealed or deleted memories
do not count. Scholar marks use a stable key and are array-unique.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

SpiritStage = Literal["whelp", "formed", "awake"]

STAGES: Final[tuple[SpiritStage, ...]] = ("whelp", "formed", "awake")
STAGE_ORDER: Final[dict[str, int]] = {"whelp": 0, "formed": 1, "awake": 2}
FORMED_BOND_MIN: Final[int] = 20
AWAKE_BOND_MIN: Final[int] = 50
FORMED_ACTIVE_MEMORY_TYPES: Final[int] = 3
STAGE_EVENT_TYPE: Final[str] = "stage.changed"
SCHOLAR_ROOM_LAYER: Final[str] = "scholar"
SCHOLAR_TITLE_SUFFIX: Final[str] = " · 学者"
SCHOLAR_MARK_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SCHOLAR_MARK_MAX_LEN: Final[int] = 64
PACT_THEMES: Final[frozenset[str]] = frozenset({"interview", "notes"})


@dataclass(frozen=True, slots=True)
class EvolutionFacts:
    stage: str
    bond: int
    active_memory_types: frozenset[str]
    has_knowledge_or_pact_growth: bool
    has_visited: bool
    has_been_lost: bool
    scholar_marks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StageAdvance:
    from_stage: str
    to_stage: str


@dataclass(frozen=True, slots=True)
class EvolutionSummaryPatch:
    stage: str
    scholar_marks: tuple[str, ...]
    room_layers_include_scholar: bool
    report_title_suffix: str | None


def formed_ready(facts: EvolutionFacts) -> bool:
    return (
        facts.bond >= FORMED_BOND_MIN
        and len(facts.active_memory_types) >= FORMED_ACTIVE_MEMORY_TYPES
    )


def awake_ready(facts: EvolutionFacts) -> bool:
    return (
        facts.bond >= AWAKE_BOND_MIN
        and facts.has_knowledge_or_pact_growth
        and (facts.has_visited or facts.has_been_lost)
    )


def next_stage(facts: EvolutionFacts) -> str | None:
    """One legal step forward. Never skip or regress."""
    if facts.stage not in STAGE_ORDER:
        raise ValueError("spirit stage is not in the catalog")
    if facts.stage == "whelp" and formed_ready(facts):
        return "formed"
    if facts.stage == "formed" and awake_ready(facts):
        return "awake"
    return None


def require_forward_step(current: str, nxt: str) -> None:
    if STAGE_ORDER.get(nxt, -1) != STAGE_ORDER.get(current, -1) + 1:
        raise ValueError("stage must advance one step without skip or regress")


def pact_scholar_mark_key(*, theme: str, question_bank_version: str) -> str:
    if theme not in PACT_THEMES:
        raise ValueError("pact theme is not in the catalog")
    version = question_bank_version.strip().lower()
    if not version:
        raise ValueError("question_bank_version is required")
    key = f"{theme}-{version}"
    return require_scholar_mark_key(key)


def require_scholar_mark_key(key: str) -> str:
    mark = key.strip().lower()
    if not mark or len(mark) > SCHOLAR_MARK_MAX_LEN or SCHOLAR_MARK_PATTERN.match(mark) is None:
        raise ValueError("scholar mark key is not stable")
    return mark


def unique_scholar_marks(marks: Sequence[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for raw in marks:
        key = require_scholar_mark_key(raw)
        if key not in seen:
            seen.append(key)
    return tuple(seen)


def apply_scholar_title_suffix(title: str, marks: Sequence[str]) -> str:
    if not unique_scholar_marks(marks):
        return title
    if title.endswith(SCHOLAR_TITLE_SUFFIX):
        return title
    return f"{title}{SCHOLAR_TITLE_SUFFIX}"


def evolution_summary_patch(
    facts: EvolutionFacts, *, stage: str | None = None
) -> EvolutionSummaryPatch:
    current = stage if stage is not None else facts.stage
    marks = unique_scholar_marks(facts.scholar_marks)
    return EvolutionSummaryPatch(
        stage=current,
        scholar_marks=marks,
        room_layers_include_scholar=bool(marks),
        report_title_suffix=SCHOLAR_TITLE_SUFFIX if marks else None,
    )
