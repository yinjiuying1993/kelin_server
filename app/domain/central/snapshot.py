"""Snapshot assembly rules. Projection consumes module results; no reverse write."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.central.types import RULES_VERSION, SpiritStatus

FORBIDDEN_SNAPSHOT_FIELDS: frozenset[str] = frozenset(
    {
        "weather",
        "motion",
        "light",
        "scholar_marks",
        "notification_actions",
        "style_samples",
        "layers",
        "pending_sight",
    }
)
ALLOWED_SNAPSHOT_FIELDS: frozenset[str] = frozenset(
    {
        "snapshot_version",
        "status",
        "hunger_band",
        "energy_band",
        "mood_band",
        "primary_emotion_label",
        "stage",
        "stage_image_key",
        "letter_key",
        "text_status",
        "rules_version",
    }
)


@dataclass(frozen=True, slots=True)
class SpiritSnapshot:
    snapshot_version: int
    status: SpiritStatus
    hunger_band: str | None
    energy_band: str | None
    mood_band: str | None
    primary_emotion_label: str | None
    stage: str
    stage_image_key: str
    letter_key: str | None
    text_status: str | None
    rules_version: str = RULES_VERSION


def reject_forbidden_fields(fields: dict[str, object]) -> None:
    banned = FORBIDDEN_SNAPSHOT_FIELDS.intersection(fields)
    if banned:
        names = ", ".join(sorted(banned))
        raise ValueError(f"forbidden snapshot field: {names}")


def assemble_snapshot(
    *,
    snapshot_version: int,
    status: SpiritStatus,
    hunger_band: str | None,
    energy_band: str | None,
    mood_band: str | None,
    primary_emotion_label: str | None,
    stage: str,
    stage_image_key: str,
    letter_key: str | None,
    text_status: str | None,
    extra: dict[str, object] | None = None,
) -> SpiritSnapshot:
    reject_forbidden_fields(extra or {})
    if snapshot_version < 1:
        raise ValueError("snapshot_version must be >= 1")
    return SpiritSnapshot(
        snapshot_version=snapshot_version,
        status=status,
        hunger_band=hunger_band,
        energy_band=energy_band,
        mood_band=mood_band,
        primary_emotion_label=primary_emotion_label,
        stage=stage,
        stage_image_key=stage_image_key,
        letter_key=letter_key,
        text_status=text_status,
        rules_version=RULES_VERSION,
    )
