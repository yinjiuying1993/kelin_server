from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.awareness.timeline import AWAY_AFTER
from app.domain.room import (
    LETTER_TITLE_KEYS,
    P08_STATUS_LAYERS,
    RoomLetterFact,
    project_room,
    project_room_letter,
)
from app.repositories import bootstrap as bootstrap_repo

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
SPIRIT = uuid4()
PROMISE = uuid4()
PACT = uuid4()
POSTCARD = uuid4()


def _fact(resource_id: UUID, hours_ago: int = 1) -> RoomLetterFact:
    return RoomLetterFact(resource_id=resource_id, occurred_at=NOW - timedelta(hours=hours_ago))


def test_letter_priority_lost_beats_all_other_candidates() -> None:
    letter = project_room_letter(
        status="lost",
        spirit_id=SPIRIT,
        last_interact_at=NOW - timedelta(hours=80),
        now=NOW,
        due_promise=_fact(PROMISE),
        pact_recap=_fact(PACT),
        unread_postcard=_fact(POSTCARD),
    )
    assert letter is not None
    assert letter.type == "lost"
    assert letter.resource_id == SPIRIT
    assert letter.title_key == LETTER_TITLE_KEYS["lost"]


def test_letter_priority_promise_then_pact_then_postcard_then_care() -> None:
    last = NOW - AWAY_AFTER
    promise = project_room_letter(
        status="home",
        spirit_id=SPIRIT,
        last_interact_at=last,
        now=NOW,
        due_promise=_fact(PROMISE),
        pact_recap=_fact(PACT),
        unread_postcard=_fact(POSTCARD),
    )
    assert promise is not None
    assert promise.type == "promise"
    assert promise.resource_id == PROMISE
    pact = project_room_letter(
        status="home",
        spirit_id=SPIRIT,
        last_interact_at=last,
        now=NOW,
        due_promise=None,
        pact_recap=_fact(PACT),
        unread_postcard=_fact(POSTCARD),
    )
    assert pact is not None
    assert pact.type == "pact"
    postcard = project_room_letter(
        status="home",
        spirit_id=SPIRIT,
        last_interact_at=last,
        now=NOW,
        due_promise=None,
        pact_recap=None,
        unread_postcard=_fact(POSTCARD),
    )
    assert postcard is not None
    assert postcard.type == "postcard"
    care = project_room_letter(
        status="home",
        spirit_id=SPIRIT,
        last_interact_at=last,
        now=NOW,
        due_promise=None,
        pact_recap=None,
        unread_postcard=None,
    )
    assert care is not None
    assert care.type == "care"
    assert care.resource_id == SPIRIT


def test_p08_four_statuses_map_to_primary_layers() -> None:
    assert P08_STATUS_LAYERS == {
        "home": "spirit",
        "study": "away",
        "away": "away",
        "lost": "lost",
    }
    last = NOW - timedelta(hours=1)
    for status, layer in P08_STATUS_LAYERS.items():
        projected = project_room(
            status=status,
            spirit_id=SPIRIT,
            last_interact_at=last,
            now=NOW,
            scholar_marks=(),
            due_promise=None,
            pact_recap=None,
            unread_postcard=None,
            pending_sight=False,
            unread_footprint_count=0,
        )
        assert projected.layers[0] == layer
        if status != "lost":
            assert projected.letter is None


def test_recent_home_has_no_letter_and_spirit_layer_only() -> None:
    projected = project_room(
        status="home",
        spirit_id=SPIRIT,
        last_interact_at=NOW - timedelta(hours=1),
        now=NOW,
        scholar_marks=(),
        due_promise=None,
        pact_recap=None,
        unread_postcard=None,
        pending_sight=False,
        unread_footprint_count=0,
    )
    assert projected.letter is None
    assert projected.layers == ("spirit",)


def test_layers_include_pending_letter_footprints_scholar_not_mark_keys() -> None:
    projected = project_room(
        status="study",
        spirit_id=SPIRIT,
        last_interact_at=NOW - timedelta(hours=1),
        now=NOW,
        scholar_marks=("interview-v1",),
        due_promise=_fact(PROMISE),
        pact_recap=None,
        unread_postcard=_fact(POSTCARD),
        pending_sight=True,
        unread_footprint_count=2,
    )
    assert projected.layers == ("away", "pending_sight", "letter", "footprints")
    assert "scholar" not in projected.layers
    assert "interview-v1" not in projected.layers
    assert projected.unread_footprint_count == 2
    assert projected.letter is not None
    assert projected.letter.title_key.startswith("room.letter.")


def test_projection_sql_does_not_select_private_bodies() -> None:
    statements = (
        bootstrap_repo.SPIRIT_SQL,
        bootstrap_repo.UNREAD_POSTCARD_SQL,
        bootstrap_repo.ACTIVE_PACT_SQL,
        bootstrap_repo.MEMORY_IDS_SQL,
    )
    blob = "\n".join(statements).lower()
    assert "messages" not in blob
    assert "content" not in blob
    assert "p.text" not in blob
    assert "summary" not in blob
    assert "pa.title" not in blob
    assert "feeds" not in blob
    assert "p.created_at" in bootstrap_repo.UNREAD_POSTCARD_SQL
