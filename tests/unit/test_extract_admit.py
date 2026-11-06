"""Extract drafts reach the table only after admit()."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.domain.extract import (
    ExtractSourceTurn,
    PriorUserMessage,
    admit_extract_drafts,
    extract_source_event_id,
)
from app.domain.memory.topic import forbidden_address_topic, preferred_address_topic
from app.domain.memory.types import MemoryIndexItem
from app.providers.types import ExtractMemoryDraft

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
WINDOW = uuid.uuid4()


def _turn(role: str, content: str) -> ExtractSourceTurn:
    return ExtractSourceTurn(id=uuid.uuid4(), role=role, content=content)


def _draft(
    summary: str,
    *,
    kind: str = "preference",
    confidence: float = 0.9,
    signal: str = "explicit",
    quote: str | None = None,
    topic: str | None = None,
) -> ExtractMemoryDraft:
    return ExtractMemoryDraft(
        type=kind,  # type: ignore[arg-type]
        summary=summary,
        normalized_topic_key=topic or f"{kind}:user:habit:general",
        source_quote=quote or summary,
        confidence=confidence,
        signal=signal,  # type: ignore[arg-type]
    )


def test_joke_low_confidence_and_spirit_voice_are_dropped() -> None:
    joke = admit_extract_drafts(
        [_draft("喜欢窗边")],
        (_turn("user", "哈哈我喜欢窗边，开玩笑"), _turn("spirit", "记下了。")),
        now=NOW,
        window_id=WINDOW,
    )
    low = admit_extract_drafts(
        [_draft("喜欢喝茶", confidence=0.5)],
        (_turn("user", "记住我喜欢喝茶"), _turn("spirit", "记下了。")),
        now=NOW,
        window_id=WINDOW,
    )
    spirit = admit_extract_drafts(
        [_draft("想去火星")],
        (_turn("user", "记住我喜欢安静"), _turn("spirit", "想去火星")),
        now=NOW,
        window_id=WINDOW,
    )
    assert joke == ()
    assert low == ()
    assert spirit == ()


def test_explicit_preference_uses_signal_salience_not_the_model_score() -> None:
    user = _turn("user", "记住我喜欢窗边")
    kept = admit_extract_drafts(
        [_draft("喜欢窗边", quote="记住我喜欢窗边")],
        (user, _turn("spirit", "记下了。")),
        now=NOW,
        window_id=WINDOW,
    )
    assert len(kept) == 1
    assert kept[0].draft.summary == "喜欢窗边"
    assert kept[0].salience == 85
    assert "salience" not in kept[0].draft.model_dump()
    assert kept[0].source_message_id == user.id


def test_window_keeps_at_most_two_admitted_drafts() -> None:
    user = _turn("user", "记住第一件。记住第二件。记住第三件。")
    kept = admit_extract_drafts(
        [
            _draft("第一件", quote="记住第一件", topic="preference:user:item_one:general"),
            _draft("第二件", quote="记住第二件", topic="preference:user:item_two:general"),
            _draft("第三件", quote="记住第三件", topic="preference:user:item_three:general"),
        ],
        (user, _turn("spirit", "记下了。")),
        now=NOW,
        window_id=WINDOW,
    )
    assert len(kept) == 2


def test_model_signal_is_not_enough_without_evidence() -> None:
    user = _turn("user", "记住我喜欢窗边")
    kept = admit_extract_drafts(
        [_draft("喜欢窗边", signal="repeated", quote="记住我喜欢窗边")],
        (user, _turn("spirit", "记下了。")),
        now=NOW,
        window_id=WINDOW,
    )
    assert len(kept) == 1
    assert kept[0].decision.memory_type == "preference"
    assert kept[0].salience == 85
    bare = admit_extract_drafts(
        [_draft("喜欢喝茶", signal="repeated", quote="喜欢喝茶")],
        (_turn("user", "喜欢喝茶"), _turn("spirit", "记下了。")),
        now=NOW,
        window_id=WINDOW,
    )
    assert bare == ()


def test_quote_outside_the_window_and_bad_topic_are_dropped() -> None:
    user = _turn("user", "记住我喜欢窗边")
    missing = admit_extract_drafts(
        [_draft("喜欢窗边", quote="想去火星")],
        (user, _turn("spirit", "记下了。")),
        now=NOW,
        window_id=WINDOW,
    )
    bad_topic = admit_extract_drafts(
        [_draft("喜欢窗边", topic="window")],
        (user, _turn("spirit", "记下了。")),
        now=NOW,
        window_id=WINDOW,
    )
    assert missing == ()
    assert bad_topic == ()


def test_same_topic_merges_and_same_event_does_not_confirm_again() -> None:
    user = _turn("user", "记住我喜欢窗边")
    topic = "preference:user:seat:window"
    existing = uuid.uuid4()
    kept = admit_extract_drafts(
        [_draft("喜欢窗边", quote="记住我喜欢窗边", topic=topic)],
        (user, _turn("spirit", "记下了。")),
        now=NOW,
        window_id=WINDOW,
        index=(
            MemoryIndexItem(
                memory_id=existing,
                memory_type="preference",
                topic_key=topic,
                summary="喜欢窗边",
                status="active",
                salience=80,
                confidence=0.9,
                confirm_count=1,
                last_confirmed_at=NOW,
                source_event_id=uuid.uuid4(),
            ),
        ),
    )
    assert len(kept) == 1
    assert kept[0].decision.decision == "merge"
    assert kept[0].decision.confirm_delta == 1
    assert kept[0].decision.conflict_target_id == existing
    assert kept[0].salience == 85

    replay = admit_extract_drafts(
        [_draft("喜欢窗边", quote="记住我喜欢窗边", topic=topic)],
        (user, _turn("spirit", "记下了。")),
        now=NOW,
        window_id=WINDOW,
        index=(
            MemoryIndexItem(
                memory_id=existing,
                memory_type="preference",
                topic_key=topic,
                summary="喜欢窗边",
                status="active",
                salience=85,
                confidence=0.9,
                confirm_count=1,
                last_confirmed_at=NOW,
                source_event_id=extract_source_event_id(WINDOW, topic),
            ),
        ),
    )
    assert len(replay) == 1
    assert replay[0].decision.decision == "merge"
    assert replay[0].decision.confirm_delta == 0
    assert replay[0].decision.conflict_target_id == existing


def test_one_worry_is_not_a_memory_until_a_second_window() -> None:
    topic = "emotion:user:mood:anxiety:current"
    draft = _draft(
        "我很焦虑",
        kind="emotion",
        signal="stable_emotion",
        quote="我很焦虑",
        topic=topic,
    )
    once = admit_extract_drafts(
        [draft],
        (_turn("user", "我很焦虑"), _turn("spirit", "我在。")),
        now=NOW,
        window_id=WINDOW,
    )
    assert once == ()
    earlier = PriorUserMessage(id=uuid.uuid4(), window_id=uuid.uuid4(), content="我很焦虑")
    twice = admit_extract_drafts(
        [draft],
        (_turn("user", "我很焦虑"), _turn("spirit", "我在。")),
        now=NOW,
        window_id=WINDOW,
        prior_messages=(earlier,),
    )
    assert len(twice) == 1
    assert twice[0].salience == 64


def test_forget_masks_one_topic_and_a_repeat_can_use_three_messages() -> None:
    tea = _draft("喜欢喝茶", quote="记住我喜欢喝茶", topic="preference:user:drink:tea")
    seat = _draft("喜欢窗边", quote="喜欢窗边", topic="preference:user:seat:window")
    kept = admit_extract_drafts(
        [tea, seat],
        (
            _turn("user", "记住我喜欢喝茶"),
            _turn("user", "别记喜欢窗边"),
            _turn("spirit", "好。"),
        ),
        now=NOW,
        window_id=WINDOW,
    )
    assert [item.draft.summary for item in kept] == ["喜欢喝茶"]
    blocked = admit_extract_drafts(
        [seat],
        (_turn("user", "记住我喜欢窗边"), _turn("spirit", "好。")),
        now=NOW,
        window_id=uuid.uuid4(),
        stored_masks=frozenset({"preference:user:seat:window"}),
    )
    assert blocked == ()
    restored = admit_extract_drafts(
        [_draft("喜欢窗边", quote="重新记住喜欢窗边", topic="preference:user:seat:window")],
        (_turn("user", "重新记住喜欢窗边"), _turn("spirit", "好。")),
        now=NOW,
        window_id=uuid.uuid4(),
        stored_masks=frozenset({"preference:user:seat:window"}),
    )
    assert len(restored) == 1
    first, second, third = (_turn("user", "窗边安静") for _ in range(3))
    repeated = admit_extract_drafts(
        [_draft("喜欢窗边", signal="repeated", quote="窗边安静", topic="preference:user:seat:window")],
        (first, second, third, _turn("spirit", "好。")),
        now=NOW,
        window_id=WINDOW,
    )
    assert len(repeated) == 1
    assert repeated[0].salience == 58


def test_address_names_use_different_topic_keys() -> None:
    preferred = admit_extract_drafts(
        [_draft("阿玉", quote="以后叫我阿玉", topic="preference:user:name:ayu")],
        (_turn("user", "以后叫我阿玉"), _turn("spirit", "好。")),
        now=NOW,
        window_id=WINDOW,
    )
    forbidden = admit_extract_drafts(
        [_draft("小周", quote="别叫我小周", topic="preference:user:name:zhou")],
        (_turn("user", "别叫我小周"), _turn("spirit", "好。")),
        now=NOW,
        window_id=WINDOW,
    )
    assert preferred[0].decision.normalized_topic_key == preferred_address_topic()
    assert forbidden[0].decision.normalized_topic_key == forbidden_address_topic()
    assert preferred_address_topic() != forbidden_address_topic()


def test_placeholder_and_hash_topics_are_rejected_and_uncertainty_only_lowers_score() -> None:
    user = _turn("user", "记住我可能喜欢窗边")
    placeholder = admit_extract_drafts(
        [_draft("喜欢窗边", quote="记住我可能喜欢窗边", topic="preference:user:item:note")],
        (user, _turn("spirit", "好。")),
        now=NOW,
        window_id=WINDOW,
    )
    hashed = admit_extract_drafts(
        [_draft("喜欢窗边", quote="记住我可能喜欢窗边", topic="preference:user:habit:abcd1234")],
        (user, _turn("spirit", "好。")),
        now=NOW,
        window_id=WINDOW,
    )
    scored = admit_extract_drafts(
        [_draft("喜欢窗边", quote="记住我可能喜欢窗边", topic="preference:user:seat:window")],
        (user, _turn("spirit", "好。")),
        now=NOW,
        window_id=WINDOW,
    )
    assert placeholder == ()
    assert hashed == ()
    assert len(scored) == 1
    assert scored[0].salience == 75
