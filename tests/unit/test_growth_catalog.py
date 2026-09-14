from inspect import getsource
from uuid import uuid4

from app.domain.feed import (
    EMOTION_EFFECTS,
    FOOD_ENERGY_DELTA,
    FOOD_HUNGER_DELTA,
    GROWTH_EVENT_TYPE,
    GROWTH_PROMISE_COMPLETED,
    GROWTH_SOURCE_TYPE,
    PROMISE_COMPLETE_BOND_DELTA,
)
from app.domain.growth import (
    FORBIDDEN_GROWTH_PAYLOAD_KEYS,
    GROWTH_CATALOG,
    GROWTH_EVENT_TYPES,
    GROWTH_PAYLOAD_FIELDS,
    MUTATION_EVENT_FOR_GROWTH,
    TRAIT_DELTA_RANGE,
    growth_catalog_by_event,
    growth_dedupe_key,
)
from app.models.engagement import GrowthEvent
from app.repositories import growth as growth_repo
from app.repositories import recall as recall_repo
from app.schemas.jsonb import parse_growth_payload
from app.services import chat as chat_service
from app.services import extract as extract_service
from app.services import feed as feed_service
from app.services import pact as pact_service
from app.services import recall as recall_service
from app.services import state_scheduler as state_scheduler_service
from pydantic import ValidationError
from pytest import raises
from sqlalchemy import CheckConstraint


def test_catalog_covers_spec_event_types_exactly_once() -> None:
    types = [entry.event_type for entry in GROWTH_CATALOG]
    assert len(types) == len(set(types))
    assert set(types) == GROWTH_EVENT_TYPES
    assert GROWTH_EVENT_TYPES == {
        "chat_completed",
        "feed_accepted",
        "promise_completed",
        "pact_answered",
        "pact_completed",
        "memory_added",
        "visit_completed",
        "returned_from_lost",
        "time_passed",
    }


def test_each_event_locks_producer_source_and_patch() -> None:
    for entry in GROWTH_CATALOG:
        assert entry.producers
        assert entry.source_id
        assert entry.source_type
        if entry.producer_status == "wired":
            assert entry.patch
        if entry.event_type == "promise_completed":
            assert "bond" in entry.effects
            assert "POST /feeds/{id}/complete" in entry.producers
        if entry.event_type == "feed_accepted":
            assert "POST /feed food|knowledge|emotion" in entry.producers
        if entry.event_type == "memory_added":
            assert entry.producer_status == "wired"
            assert entry.payload == "trait_delta"
            assert "POST /extract" in entry.producers
        if entry.event_type == "time_passed":
            assert entry.producer_status == "wired"
            assert entry.source_type == "scheduler"
            assert entry.payload == "empty"
            assert "state.settle scheduler" in entry.producers[0]
        if entry.event_type == "returned_from_lost":
            assert entry.producer_status == "wired"
            assert entry.source_type == "recall"
            assert "POST /recall" in entry.producers


def test_dedupe_key_is_source_type_source_id_event_type() -> None:
    source_id = uuid4()
    key = growth_dedupe_key(source_type="feed", source_id=source_id, event_type="feed_accepted")
    assert key == ("feed", source_id, "feed_accepted")
    with raises(ValueError, match="source_type"):
        growth_dedupe_key(source_type="feed", source_id=source_id, event_type="chat_completed")
    with raises(ValueError, match="event_type"):
        growth_dedupe_key(source_type="feed", source_id=source_id, event_type="toy")


def test_orm_event_type_check_matches_catalog() -> None:
    constraint = next(
        item
        for item in GrowthEvent.__table_args__
        if isinstance(item, CheckConstraint) and item.name == "ck_growth_events_event_type"
    )
    sql = str(constraint.sqltext)
    for event_type in GROWTH_EVENT_TYPES:
        assert f"'{event_type}'" in sql


def test_wired_producers_insert_catalog_keys() -> None:
    insert_sql = getsource(growth_repo.insert_growth_event)
    assert "ON CONFLICT (source_type, source_id, event_type)" in insert_sql
    apply_sql = getsource(growth_repo.apply_growth_to_spirit)
    assert "GREATEST(0, LEAST(100" in apply_sql
    assert "record_and_apply" in getsource(feed_service._record_growth)
    feed_src = getsource(feed_service)
    assert "GROWTH_EVENT_TYPE" in feed_src
    assert "GROWTH_PROMISE_COMPLETED" in feed_src
    assert "record_and_apply" in feed_src
    assert "chat_completed" in getsource(chat_service)
    assert "record_and_apply" in getsource(chat_service)
    assert "memory_added" in getsource(extract_service)
    assert "record_and_apply" in getsource(extract_service)
    assert "touch_interact=False" in getsource(chat_service)
    assert "time_passed" in getsource(state_scheduler_service)
    assert "touch_interact=False" in getsource(state_scheduler_service)
    assert "kelin_scheduler" in getsource(state_scheduler_service)
    pact_src = getsource(pact_service)
    assert "PACT_ANSWERED_GROWTH" in pact_src
    assert "PACT_COMPLETED_GROWTH" in pact_src
    assert "record_and_apply" in pact_src
    recall_src = getsource(recall_service)
    assert "RECALL_GROWTH_EVENT" in recall_src
    assert "record_and_apply" in recall_src
    assert "status = 'home'" in getsource(recall_repo.mark_returned_home)
    assert GROWTH_SOURCE_TYPE == growth_catalog_by_event()["feed_accepted"].source_type
    assert GROWTH_EVENT_TYPE == "feed_accepted"
    assert GROWTH_PROMISE_COMPLETED == "promise_completed"
    assert growth_catalog_by_event()["memory_added"].source_type == "memory"


def test_http_event_names_are_not_ledger_event_types() -> None:
    assert MUTATION_EVENT_FOR_GROWTH["feed_accepted"] == "feed.accepted"
    assert MUTATION_EVENT_FOR_GROWTH["promise_completed"] == "promise.completed"
    assert MUTATION_EVENT_FOR_GROWTH["pact_answered"] == "pact.answered"
    assert MUTATION_EVENT_FOR_GROWTH["pact_completed"] == "pact.completed"
    assert MUTATION_EVENT_FOR_GROWTH["returned_from_lost"] == "recall.returned"
    assert "feed.accepted" not in GROWTH_EVENT_TYPES


def test_payload_allows_only_settlement_scalars() -> None:
    parse_growth_payload({})
    parse_growth_payload({"hunger_delta": FOOD_HUNGER_DELTA, "energy_delta": FOOD_ENERGY_DELTA})
    parse_growth_payload({"mood_delta": EMOTION_EFFECTS["angry"].mood_delta})
    parse_growth_payload({"bond_delta": PROMISE_COMPLETE_BOND_DELTA})
    parse_growth_payload({"closeness_delta": TRAIT_DELTA_RANGE[1]})
    with raises(ValidationError):
        parse_growth_payload({"closeness_delta": TRAIT_DELTA_RANGE[1] + 1})
    for key in FORBIDDEN_GROWTH_PAYLOAD_KEYS:
        with raises(ValidationError):
            parse_growth_payload({key: "secret"})
    assert GROWTH_PAYLOAD_FIELDS == {
        "hunger_delta",
        "energy_delta",
        "mood_delta",
        "bond_delta",
        "closeness_delta",
        "curiosity_delta",
        "sharpness_delta",
        "nocturnal_delta",
        "stubborn_delta",
    }
