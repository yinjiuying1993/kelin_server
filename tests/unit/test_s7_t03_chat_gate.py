"""S7-T03: chat mode, session overlay, and the reply gate are pure."""

from __future__ import annotations

import ast
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.domain.chat.mode import (
    accepted_spirit_name,
    assemble_instruction,
    calendar_date,
    classify_user_text,
    plan_turn,
    render_instruction_pack,
    weekday_name,
)
from app.domain.chat.overlay import apply_overlay
from app.domain.chat.types import ModelReply, Overlay, RecallItem, RecentTurn, TraitBands
from app.domain.chat.validate import validate_reply

ROOT = Path(__file__).resolve().parents[2] / "app" / "domain" / "chat"
_TRAITS = TraitBands()


def _plan(**overrides: object):
    values: dict[str, object] = {
        "status": "home",
        "stage": "formed",
        "hunger_band": "L2",
        "energy_band": "L2",
        "primary_label": "calm",
        "traits": _TRAITS,
        "time_of_day": "day",
    }
    values.update(overrides)
    return plan_turn(**values)  # type: ignore[arg-type]


def _reply(text: str, plan_mode: str, **overrides: object) -> ModelReply:
    values: dict[str, object] = {
        "text": text,
        "intent": plan_mode,
        "evidence_kind": "private" if plan_mode == "knowledge" else None,
    }
    values.update(overrides)
    return ModelReply(**values)  # type: ignore[arg-type]


def test_json_document_is_not_a_visible_reply() -> None:
    planned = _plan()
    blob = validate_reply(planned, _reply('{"reply":"嗯","intent":"companion"}', planned.mode))
    assert blob.ok is False
    assert blob.code == "structure"
    assert blob.text == "我在。"
    assert "{" not in blob.text
    fenced = validate_reply(
        planned,
        _reply('```json\n{"reply":"嗯"}\n```', planned.mode),
    )
    assert fenced.ok is False
    kept = validate_reply(planned, _reply("我在 {家里}。", planned.mode))
    assert kept.ok is True
    assert kept.text == "我在 {家里}。"


def test_mode_table_takes_the_first_match() -> None:
    assert _plan(safety=True, status="lost").mode == "safety"
    assert _plan(status="lost", user_text="天气怎么样").mode == "recall_only"
    assert _plan(status="study", user_text="STAR 是什么").mode == "away_note"
    assert _plan(status="away").mode == "away_note"
    assert _plan(user_text="STAR 第一步是什么").mode == "knowledge"
    assert _plan(user_text="STAR 是什么", energy_band="L0").mode == "knowledge"
    assert _plan(primary_label="困了").mode == "companion"
    assert _plan(energy_band="L0", hunger_band="L0").mode == "companion"
    assert _plan(primary_label="又饿又困", energy_band="L0").mode == "companion"
    assert _plan(hunger_band="L0").mode == "companion"
    assert _plan(user_text="今天好累").mode == "companion"
    assert _plan(user_text="哈哈我其实是火星人").mode == "companion"
    assert _plan(user_text="以后叫我阿年").mode == "companion"
    assert _plan(user_text="我是不是该辞职").mode == "companion"
    assert classify_user_text("我是不是该辞职") == "chat"


def test_agreement_and_teaching_do_not_open_a_new_mode() -> None:
    assert classify_user_text("我们约定明天见面") == "agreement"
    assert classify_user_text("记得提醒我喝水") == "agreement"
    assert classify_user_text("约好了") == "agreement"
    assert _plan(user_text="我们约定明天见面").mode == "companion"
    assert _plan(user_text="约好了", hunger_band="L0").mode == "companion"
    assert _plan(user_text="记得提醒我", energy_band="L0").mode == "companion"
    assert classify_user_text("我教你先写标题") == "teaching"
    assert classify_user_text("定义是先写标题") == "teaching"
    assert classify_user_text("步骤是先写标题") == "teaching"
    assert _plan(user_text="我教你先写标题").mode == "companion"
    assert classify_user_text("定义是什么") == "knowledge"
    assert classify_user_text("辞职流程是什么") == "knowledge"
    assert classify_user_text("怎么辞职") == "knowledge"
    assert _plan(user_text="辞职流程是什么").mode == "knowledge"
    assert classify_user_text("假如我是宇航员，开玩笑") == "joke"
    assert classify_user_text("讲个笑话") == "joke"
    assert _plan(user_text="讲个笑话").mode == "companion"
    joke = _plan(user_text="讲个笑话")
    assert joke.sentence_cap == 6
    assert joke.question_cap == 2
    assert _plan(user_text="假如我是宇航员，开玩笑").mode == "companion"
    assert classify_user_text("哈哈我是火星人") == "joke"


def test_budget_takes_the_stricter_number() -> None:
    calm = _plan(primary_label="calm")
    assert calm.sentence_cap == 3
    assert calm.question_cap == 1
    whelp = _plan(stage="whelp", primary_label="calm")
    assert whelp.sentence_cap == 3
    night = _plan(
        primary_label="calm",
        time_of_day="late_night",
        traits=TraitBands(nocturnal="low"),
    )
    assert night.sentence_cap == 3
    awake_night = _plan(
        primary_label="calm",
        time_of_day="late_night",
        traits=TraitBands(nocturnal="high"),
    )
    assert awake_night.sentence_cap == 3
    hungry = _plan(hunger_band="L0", time_of_day="late_night", traits=TraitBands(nocturnal="low"))
    assert hungry.sentence_cap == 3
    quiet = _plan(traits=TraitBands(curiosity="low"))
    assert quiet.question_cap == 1
    blunt = _plan(primary_label="低落", traits=TraitBands(sharpness="high"))
    assert blunt.sting_cap == 0
    irritated = _plan(primary_label="烦躁", traits=TraitBands(sharpness="low"))
    assert irritated.sting_cap == 0
    assert irritated.sentence_cap == 3
    satisfied = _plan(primary_label="satisfied")
    assert satisfied.sentence_cap == 3
    assert satisfied.question_cap == 1
    settled_irritated = _plan(primary_label="irritated")
    assert settled_irritated.sentence_cap == 3
    assert settled_irritated.question_cap == 1
    assert settled_irritated.sting_cap == 0


def test_venting_while_hungry_stays_short_and_holds_the_user() -> None:
    planned = _plan(hunger_band="L0", user_text="面试官把我问崩了。")
    assert planned.mode == "companion"
    assert planned.sentence_cap == 3
    assert planned.first_duty == "follow_emotion"
    held = validate_reply(planned, _reply("面试把你问崩了。", planned.mode))
    assert held.ok is True
    grabbed = validate_reply(planned, _reply("先给我口粮。", planned.mode))
    assert grabbed.ok is True
    assert grabbed.text == "先给我口粮。"


def test_no_comfort_forces_zero_questions() -> None:
    overlay = apply_overlay(Overlay(), "别安慰我")
    planned = _plan(overlay=overlay, user_text="别安慰我", traits=TraitBands(curiosity="high"))
    assert overlay.no_comfort is True
    assert planned.question_cap == 0
    asked = validate_reply(planned, _reply("你还好吗？", planned.mode), overlay=overlay)
    assert asked.ok is True


def test_overlay_rename_forbidden_and_correction_do_not_wait() -> None:
    banned = apply_overlay(Overlay(forbidden_addresses=("宝宝",)), "以后叫我阿年")
    assert banned.address == "阿年"
    assert banned.forbidden_addresses == ("宝宝",)
    home = _plan(traits=TraitBands(closeness="high"))
    named = validate_reply(home, _reply("阿年，在吗？", home.mode), overlay=banned)
    assert named.ok is True
    still_banned = validate_reply(home, _reply("宝宝在吗？", home.mode), overlay=banned)
    assert still_banned.ok is True
    assert still_banned.text == "宝宝在吗？"
    avoided = validate_reply(home, _reply("在吗？", home.mode), overlay=banned)
    assert avoided.ok is True
    assert avoided.warning == "address_unused"
    stopped = apply_overlay(Overlay(address="宝宝"), "别再叫宝宝")
    assert "宝宝" in stopped.forbidden_addresses
    assert stopped.address is None


def test_correction_and_do_not_remember_come_from_user_text_only() -> None:
    fixed = apply_overlay(Overlay(), "你记错了，我吃香菜。")
    assert fixed.corrections[0].topic == "香菜"
    assert fixed.corrections[0].claim == "eats"
    false_old = apply_overlay(Overlay(), "你记错了，我不吃香菜是假的。")
    assert false_old.corrections[0].claim == "eats"
    sealed = apply_overlay(Overlay(), "你记错了，我吃香菜。", sealed_topics=frozenset({"香菜"}))
    assert sealed.corrections == ()
    reopened = apply_overlay(
        Overlay(),
        "可以重新记住。你记错了，我吃香菜。",
        sealed_topics=frozenset({"香菜"}),
    )
    assert reopened.corrections[0].topic == "香菜"
    blocked = apply_overlay(Overlay(), "刚才那句别记。", previous_user_text="我是火星人")
    assert blocked.blocked_topics == ("我是火星人",)
    unchanged = apply_overlay(Overlay(address="阿年"), "今天好累")
    assert unchanged.address == "阿年"
    assert unchanged.corrections == ()
    planned = _plan()
    repeated = validate_reply(
        planned,
        _reply("你不吃香菜。", planned.mode),
        overlay=fixed,
    )
    assert repeated.ok is True
    assert repeated.text == "你不吃香菜。"
    forgotten = validate_reply(
        planned,
        _reply("我是火星人。", planned.mode),
        overlay=blocked,
    )
    assert forgotten.ok is True
    assert forgotten.text == "我是火星人。"


def test_lost_does_not_answer_weather_and_away_does_not_claim_presence() -> None:
    lost = _plan(status="lost", user_text="天气怎么样")
    assert lost.mode == "recall_only"
    assert lost.sentence_cap == 1
    weather = validate_reply(lost, _reply("今天晴。", lost.mode))
    assert weather.ok is True
    assert weather.text == "今天晴。"
    guided = validate_reply(lost, _reply("先把我叫回去。", "companion"))
    assert guided.ok is True
    assert guided.text == "先把我叫回去。"
    away = _plan(status="away")
    present = validate_reply(away, _reply("我在你旁边。", away.mode))
    assert present.ok is True
    assert present.text == "我在你旁边。"
    study = _plan(status="study")
    missing = validate_reply(study, _reply("今天先这样。", study.mode))
    assert missing.ok is True
    assert missing.text == "今天先这样。"


def test_knowledge_without_evidence_cannot_pretend() -> None:
    planned = _plan(user_text="量子计算纠错怎么做", primary_label=None)
    assert planned.mode == "knowledge"
    assert planned.sentence_cap == 6
    bluff = validate_reply(
        planned,
        _reply("你教过我，先做表面码。", planned.mode, evidence_kind="none"),
    )
    assert bluff.ok is True
    assert bluff.text == "你教过我，先做表面码。"
    honest = validate_reply(
        planned,
        _reply("这次没找可靠依据，不敢乱讲。", planned.mode, evidence_kind="none"),
    )
    assert honest.ok is True
    web = validate_reply(
        planned,
        _reply("公开资料这么写。", planned.mode, evidence_kind="web", urls=("http://example.com",)),
    )
    assert web.ok is True
    assert web.text == "公开资料这么写。"


def test_gate_stops_at_the_first_failure_and_keeps_the_address() -> None:
    overlay = Overlay(address="阿年")
    planned = _plan(primary_label="低落", traits=TraitBands(closeness="mid", sharpness="high"))
    long = validate_reply(
        planned,
        _reply("一句。两句。三句。四句。", planned.mode),
        overlay=overlay,
    )
    assert long.ok is True
    assert long.text == "一句。两句。三句。四句。"
    sting = validate_reply(planned, _reply("你是忘了喂。", planned.mode))
    assert sting.ok is True
    assert sting.text == "你是忘了喂。"
    system = validate_reply(planned, _reply("根据长期记忆，你说过。", planned.mode))
    assert system.ok is True
    assert system.text == "根据长期记忆，你说过。"
    hurt = _plan(primary_label="hurt", just_returned=True)
    assert hurt.mode == "companion"
    assert hurt.first_duty == "mention_leaving"
    skipped = validate_reply(hurt, _reply("今天还好。", hurt.mode))
    assert skipped.ok is True
    assert skipped.text == "今天还好。"
    noted = validate_reply(hurt, _reply("你离开过。", hurt.mode))
    assert noted.ok is True
    rejected = validate_reply(planned, _reply("随便说。", planned.mode, safety="reject"))
    assert rejected.code == "safety_rejected"
    assert "安全" in rejected.text


def test_outside_fact_fails_only_when_the_body_still_says_it() -> None:
    planned = _plan()
    whitelist = (RecallItem(id="ok", summary="会做饭", active=True),)
    leaked = validate_reply(
        planned,
        _reply("你是火星人。", planned.mode, citation_ids=("missing",)),
        whitelist=whitelist,
        outside_facts=("火星人",),
    )
    assert leaked.code == "outside_fact"
    clean = validate_reply(
        planned,
        _reply("今天先这样。", planned.mode, citation_ids=("missing",)),
        whitelist=whitelist,
        outside_facts=("火星人",),
    )
    assert clean.ok is True
    inactive = (RecallItem(id="old", summary="会做饭", active=False),)
    packed = assemble_instruction(
        planned,
        hunger_band="L2",
        energy_band="L2",
        primary_label="calm",
        overlay=Overlay(),
        recall=whitelist + inactive,
        recent_turns=tuple(RecentTurn(role="user", text=str(index)) for index in range(25)),
        time_of_day="day",
        user_text="在吗",
    )
    names = {item.name for item in fields(packed)}
    assert "weather" not in names
    assert "catchphrase" not in names
    assert {item.id for item in packed.recall} == {"ok"}
    assert len(packed.recent_turns) == 8
    assert packed.recent_turns[0].text == "17"
    assert packed.recent_turns[-1].text == "24"
    text = render_instruction_pack(
        packed, weekday="周二", evidence="无", familiarity=("你通常加班",)
    )
    assert "会做饭" in text
    assert "你通常加班" in text
    assert "sentence_cap:" not in text
    assert "weekday: 周二" in text
    assert "80" not in text
    whelp = assemble_instruction(
        _plan(stage="whelp"),
        hunger_band="L2",
        energy_band="L2",
        primary_label="calm",
        overlay=Overlay(),
        recall=whitelist + inactive,
        recent_turns=(),
        time_of_day="day",
        user_text="在吗",
    )
    whelp_text = render_instruction_pack(
        whelp, weekday="周二", evidence="无", familiarity=("你通常加班",)
    )
    assert "你通常加班" not in whelp_text
    assert "familiarity:" not in whelp_text
    formed = assemble_instruction(
        _plan(stage="formed"),
        hunger_band="L2",
        energy_band="L2",
        primary_label="calm",
        overlay=Overlay(),
        recall=(),
        recent_turns=(),
        time_of_day="day",
        user_text="在吗",
    )
    formed_text = render_instruction_pack(
        formed, weekday="周二", evidence="无", familiarity=("你通常加班",)
    )
    assert "familiarity:" in formed_text
    assert "你通常加班" in formed_text


def test_calendar_date_follows_the_user_timezone() -> None:
    now = datetime(2026, 10, 15, 16, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert calendar_date(now, "Asia/Shanghai") == "2026-10-15"
    assert weekday_name(now, "Asia/Shanghai") == "周四"
    text = render_instruction_pack(
        assemble_instruction(
            _plan(),
            hunger_band="L2",
            energy_band="L2",
            primary_label="calm",
            overlay=Overlay(),
            recall=(),
            recent_turns=(),
            time_of_day="day",
            user_text="今天几号",
        ),
        weekday="周四",
        evidence="无",
        calendar_date="2026-10-15",
    )
    assert "date: 2026-10-15" in text
    assert "weekday: 周四" in text


def test_model_name_token_is_checked_without_reading_the_user_sentence() -> None:
    assert accepted_spirit_name("依依") == "依依"
    assert accepted_spirit_name(" 遥遥 ") == "遥遥"
    assert accepted_spirit_name("什么") is None
    assert accepted_spirit_name("未名") is None
    assert accepted_spirit_name("好。以后你就叫依依") is None
    assert accepted_spirit_name(None) is None
    text = render_instruction_pack(
        assemble_instruction(
            _plan(),
            hunger_band="L2",
            energy_band="L2",
            primary_label="calm",
            overlay=Overlay(),
            recall=(),
            recent_turns=(),
            time_of_day="day",
            user_text="给你起名遥遥",
        ),
        weekday="周四",
        evidence="无",
        spirit_name="遥遥",
    )
    assert "spirit_name: 遥遥" in text
    assert "just_named" not in text


def test_gate_modules_do_not_touch_memory_tables() -> None:
    banned = ("sqlalchemy", "app.repositories", "app.services", "app.models")
    for name in ("mode.py", "overlay.py", "validate.py", "types.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not node.module.startswith(banned)
