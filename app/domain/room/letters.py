"""Pick one homepage letter key. Does not invent lost/due/pact facts."""

from __future__ import annotations

from app.domain.central.types import SpiritStatus

LETTER_PRIORITY: tuple[str, ...] = (
    "promise_due",
    "pact_ready",
    "care_in_app",
    "study_pact",
    "away_visit",
)

LETTER_ALIASES: dict[str, str] = {
    "lost": "lost_recall",
    "lost_recall": "lost_recall",
    "promise": "promise_due",
    "promise_due": "promise_due",
    "due_promise": "promise_due",
    "pact": "pact_ready",
    "pact_ready": "pact_ready",
    "pact_recap": "pact_ready",
    "study": "study_pact",
    "study_pact": "study_pact",
    "away": "away_visit",
    "away_visit": "away_visit",
    "care": "care_in_app",
    "care_in_app": "care_in_app",
}

STATUS_DEFAULT_LETTER: dict[SpiritStatus, str] = {
    "study": "study_pact",
    "away": "away_visit",
}


def pick_letter_key(*, status: SpiritStatus, candidates: tuple[str, ...]) -> str | None:
    keys: set[str] = set()
    for item in candidates:
        mapped = LETTER_ALIASES.get(item)
        if mapped is not None:
            keys.add(mapped)
    default = STATUS_DEFAULT_LETTER.get(status)
    if default is not None:
        keys.add(default)
    for key in LETTER_PRIORITY:
        if key in keys:
            return key
    return None
