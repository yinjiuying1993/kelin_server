from __future__ import annotations

import uuid

from app.domain.memory.correct import CorrectionPeer, plan_memory_correction

CURRENT = uuid.uuid4()
OTHER = uuid.uuid4()
TEA = "knowledge:tea:method:general"
PREFERRED = "preference:user:address_name:preferred"


def _peer(
    memory_id: uuid.UUID,
    topic: str,
    summary: str,
    *,
    status: str = "active",
    salience: int = 70,
) -> CorrectionPeer:
    return CorrectionPeer(
        memory_id=memory_id,
        topic_key=topic,
        summary=summary,
        status=status,
        salience=salience,
    )


def test_naming_correction_uses_the_preferred_address_key() -> None:
    planned = plan_memory_correction(
        memory_id=CURRENT,
        memory_type="preference",
        current_topic=None,
        current_salience=80,
        summary="请叫我阿年",
        peers=(),
    )
    assert not isinstance(planned, str)
    assert planned.topic_key == PREFERRED
    assert planned.summary == "请叫我阿年"
    assert planned.supersede_ids == ()
    assert planned.confirm_delta == 0
    assert planned.salience == 80


def test_privacy_and_unstable_topic_are_rejected() -> None:
    leaked = plan_memory_correction(
        memory_id=CURRENT,
        memory_type="preference",
        current_topic=PREFERRED,
        current_salience=80,
        summary="身份证110101199003078888",
        peers=(),
    )
    assert leaked == "INVALID_INPUT"
    unstable = plan_memory_correction(
        memory_id=CURRENT,
        memory_type="knowledge",
        current_topic=None,
        current_salience=50,
        summary="茶要先醒",
        peers=(),
    )
    assert unstable == "INVALID_INPUT"


def test_existing_topic_stays_when_the_summary_is_not_a_new_name() -> None:
    planned = plan_memory_correction(
        memory_id=CURRENT,
        memory_type="knowledge",
        current_topic=TEA,
        current_salience=55,
        summary="茶要先醒",
        peers=(),
    )
    assert not isinstance(planned, str)
    assert planned.topic_key == TEA
    assert planned.confirm_delta == 0


def test_new_address_supersedes_the_old_one_without_raising_salience() -> None:
    planned = plan_memory_correction(
        memory_id=CURRENT,
        memory_type="preference",
        current_topic=None,
        current_salience=60,
        summary="请叫我阿年",
        peers=(_peer(OTHER, PREFERRED, "以后叫我小满", salience=90),),
    )
    assert not isinstance(planned, str)
    assert planned.supersede_ids == (OTHER,)
    assert planned.confirm_delta == 0
    assert planned.salience == 60


def test_same_summary_merges_and_keeps_the_higher_salience() -> None:
    planned = plan_memory_correction(
        memory_id=CURRENT,
        memory_type="preference",
        current_topic=None,
        current_salience=60,
        summary="请叫我阿年",
        peers=(_peer(OTHER, PREFERRED, "请叫我阿年", salience=90),),
    )
    assert not isinstance(planned, str)
    assert planned.supersede_ids == (OTHER,)
    assert planned.confirm_delta == 1
    assert planned.salience == 90


def test_sealed_topic_blocks_the_correction() -> None:
    planned = plan_memory_correction(
        memory_id=CURRENT,
        memory_type="knowledge",
        current_topic="knowledge:user:lesson:general",
        current_salience=55,
        summary="改成另一句",
        peers=(_peer(OTHER, "knowledge:user:lesson:general", "旧句", status="sealed"),),
    )
    assert planned == "INVALID_INPUT"
