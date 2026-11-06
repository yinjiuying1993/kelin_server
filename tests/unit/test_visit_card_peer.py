"""Visit cards keep peer.stage and always publish formed."""

from datetime import UTC, datetime
from uuid import uuid4

from app.repositories.visit_cards import VisitCardReplyRow, VisitCardSealedRow, VisitFootprintRow
from app.services.visit_cards import footprint_from_row, reply_result_from_row, sealed_from_row

_NOW = datetime(2026, 10, 23, 4, tzinfo=UTC)
_SECRET = b"kelin-test-peer-stage"


def test_peer_stage_is_formed_for_detail_reply_and_list() -> None:
    card_id = uuid4()
    reply = VisitCardReplyRow(
        id=uuid4(),
        status="open",
        body=None,
        voice_key=None,
        voice_seconds=None,
        voice_title=None,
        image_key=None,
        image_title=None,
        image_caption=None,
        reply_title=None,
        client_id=uuid4(),
    )
    sealed = VisitCardSealedRow(
        id=card_id,
        status="sealed",
        is_default=False,
        official=False,
        body="来过。",
        voice_key=None,
        voice_seconds=None,
        voice_title=None,
        image_key=None,
        image_title=None,
        image_caption=None,
        unread=True,
        occurred_at=_NOW,
        peer_title="暖暖",
        peer_stage="whelp",
        reply=None,
    )
    footprint = VisitFootprintRow(
        id=card_id,
        kind="visit",
        official=False,
        unread=True,
        occurred_at=_NOW,
        peer_title="暖暖",
        peer_stage="awake",
        is_default=False,
        body="来过。",
        voice_key=None,
        voice_seconds=None,
        voice_title=None,
        image_key=None,
        image_title=None,
        image_caption=None,
        reply_title=None,
        reply_status=None,
        reply_body=None,
    )
    assert sealed_from_row(sealed, secret=_SECRET, now=_NOW).peer.stage == "formed"
    assert footprint_from_row(footprint, secret=_SECRET, now=_NOW).peer.stage == "formed"
    answered = reply_result_from_row(reply, sealed, secret=_SECRET, now=_NOW)
    assert answered.peer.stage == "formed"
