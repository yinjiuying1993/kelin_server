from uuid import uuid4

import pytest
from app.schemas.jsonb import parse_feed_payload, parse_growth_payload, parse_source_refs
from pydantic import ValidationError


def test_feed_payload_discriminated_union_accepts_spec_examples() -> None:
    parse_feed_payload(kind="food", payload={})
    parse_feed_payload(kind="knowledge", payload={"text": "今天学了圆周率"})
    parse_feed_payload(kind="emotion", payload={"emotion": "anxious", "note": "面试"})
    parse_feed_payload(
        kind="promise",
        payload={"text": "明天运动", "remind_at": "2026-09-08T12:00:00Z"},
    )
    parse_feed_payload(kind="sight", payload={"source": "photo"})
    parse_feed_payload(
        kind="sight",
        payload={
            "source": "location",
            "label": "外滩",
            "city": "上海",
            "category": "landmark",
        },
    )


def test_feed_payload_rejects_unknown_kind_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        parse_feed_payload(kind="toy", payload={})
    with pytest.raises(ValidationError):
        parse_feed_payload(kind="food", payload={"calories": 1})
    with pytest.raises(ValidationError):
        parse_feed_payload(kind="emotion", payload={"emotion": "excited"})
    with pytest.raises(ValidationError):
        parse_feed_payload(kind="knowledge", payload={"text": ""})
    parse_feed_payload(kind="knowledge", payload={"text": "字" * 10000})
    with pytest.raises(ValidationError):
        parse_feed_payload(kind="knowledge", payload={"text": "字" * 10001})


def test_sight_location_forbids_coordinates_and_address() -> None:
    with pytest.raises(ValidationError):
        parse_feed_payload(
            kind="sight",
            payload={
                "source": "location",
                "label": "外滩",
                "city": "上海",
                "latitude": 31.23,
                "longitude": 121.49,
            },
        )
    with pytest.raises(ValidationError):
        parse_feed_payload(
            kind="sight",
            payload={"source": "photo", "latitude": 31.23},
        )
    with pytest.raises(ValidationError):
        parse_feed_payload(
            kind="sight",
            payload={
                "source": "location",
                "label": "外滩",
                "city": "上海",
                "provider": {"place_id": "amap-1"},
            },
        )
    with pytest.raises(ValidationError):
        parse_feed_payload(
            kind="sight",
            payload={
                "source": "location",
                "label": "外滩",
                "city": "上海",
                "address": "中山东一路",
            },
        )


def test_source_refs_only_allow_verified_citation_summaries() -> None:
    parse_source_refs([])
    parse_source_refs([{"type": "memory", "id": str(uuid4())}])
    with pytest.raises(ValidationError):
        parse_source_refs({"type": "memory"})
    with pytest.raises(ValidationError):
        parse_source_refs([{"type": "memory", "id": str(uuid4()), "content": "秘密"}])
    with pytest.raises(ValidationError):
        parse_source_refs([{"type": "search", "url": "https://example.com"}])
    with pytest.raises(ValidationError):
        parse_source_refs([{"type": "web", "url": "http://example.com"}])
    web = parse_source_refs(
        [
            {
                "type": "web",
                "url": "https://example.com/a",
                "fetched_at": "2026-09-10T04:00:00Z",
            }
        ]
    )
    assert web[0].url == "https://example.com/a"
    with pytest.raises(ValidationError):
        parse_source_refs([{"type": "memory", "id": str(uuid4()), "url": "https://example.com"}])


def test_growth_payload_rejects_message_body() -> None:
    parse_growth_payload({})
    parse_growth_payload({"bond_delta": 1, "hunger_delta": -5})
    with pytest.raises(ValidationError):
        parse_growth_payload({"text": "用户说了晚安"})
    with pytest.raises(ValidationError):
        parse_growth_payload({"content": "完整对话"})
    with pytest.raises(ValidationError):
        parse_growth_payload({"prompt": "system prompt"})
