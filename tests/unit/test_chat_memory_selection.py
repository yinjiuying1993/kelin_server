"""Stage cap and topic match for the chat instruction pack."""

from __future__ import annotations

from app.domain.chat.selection import ChatMemory, select_chat_memories


def _memory(memory_id: str, summary: str, *, salience: int = 10) -> ChatMemory:
    return ChatMemory(
        id=memory_id,
        memory_type="preference",
        summary=summary,
        salience=salience,
    )


def test_whelp_keeps_three_related_and_drops_high_salience_noise() -> None:
    noise = tuple(
        _memory(f"n{index}", f"高显著天文台观测{index}", salience=99) for index in range(6)
    )
    related = tuple(_memory(str(index), f"周末爬山路线{index}", salience=10) for index in range(4))
    picked = select_chat_memories(
        noise + related,
        stage="whelp",
        user_text="周末爬山",
        recent_user_texts=(),
    )
    assert len(picked) == 3
    assert all("周末爬山" in item.summary for item in picked)
    assert all(item.salience == 10 for item in picked)


def test_forced_slots_count_toward_the_stage_cap() -> None:
    picked = select_chat_memories(
        (
            _memory("address", "称呼是阿年"),
            _memory("banned", "别再叫宝宝"),
            _memory("mine", "加班是雷区"),
            _memory("trip", "周末爬山路线"),
            _memory("noise", "无关的库存清单", salience=99),
        ),
        stage="whelp",
        user_text="周末爬山",
        recent_user_texts=(),
        address="阿年",
        forbidden=("宝宝",),
    )
    assert [item.id for item in picked] == ["address", "banned", "mine"]


def test_recent_user_text_can_keep_a_memory() -> None:
    picked = select_chat_memories(
        (_memory("cat", "猫叫小满"),),
        stage="formed",
        user_text="在吗",
        recent_user_texts=("昨天", "小满吃饭了"),
    )
    assert [item.id for item in picked] == ["cat"]


def test_formed_cap_is_five() -> None:
    items = tuple(_memory(str(index), f"周末爬山路线{index}") for index in range(8))
    picked = select_chat_memories(
        items,
        stage="formed",
        user_text="周末爬山",
        recent_user_texts=(),
    )
    assert len(picked) == 5
