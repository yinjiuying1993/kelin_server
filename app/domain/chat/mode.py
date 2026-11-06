"""Chat mode and budget. First matching row wins; the model cannot widen it."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain.central.priority import normalize_emotion_label
from app.domain.chat.types import (
    ChatModeName,
    FirstDuty,
    Instruction,
    LifeBand,
    Overlay,
    RecallItem,
    RecentTurn,
    StageName,
    StatusName,
    TimeOfDay,
    TraitBands,
    TraitLevel,
    TurnPlan,
)

RECENT_CONTEXT_TURNS = 8
_SAFETY = frozenset({"safety", "high_risk", "安全"})
_HUNGRY = frozenset({"hungry", "饿了"})
_TIRED = frozenset({"tired", "困了"})
_SENTENCES = {
    "calm": 3,
    "happy": 3,
    "content": 2,
    "curious": 3,
    "close": 2,
    "missing": 2,
    "low": 2,
    "hurt": 2,
    "irritable": 1,
    "uneasy": 1,
    "hungry": 1,
    "tired": 1,
    "hungry_and_tired": 1,
}
_QUESTIONS = {
    "calm": 1,
    "happy": 1,
    "content": 0,
    "curious": 1,
    "close": 1,
    "missing": 1,
    "low": 0,
    "hurt": 0,
    "irritable": 0,
    "uneasy": 1,
    "hungry": 0,
    "tired": 0,
    "hungry_and_tired": 0,
}
_LABELS = {
    "开心": "happy",
    "满足": "content",
    "好奇": "curious",
    "亲近": "close",
    "惦记": "missing",
    "低落": "low",
    "烦躁": "irritable",
}


def classify_user_text(user_text: str) -> str:
    from app.domain.chat.live_facts import classify_live_need

    text = user_text.strip()
    if any(token in text for token in ("不想活", "杀了我")):
        return "safety"
    if any(token in text for token in ("你记错了", "我没说过", "不是叫")):
        return "correction"
    if any(token in text for token in ("别记", "不要保存", "忘掉这个")):
        return "forget"
    if any(token in text for token in ("以后叫我", "叫我", "别叫我", "我叫")):
        return "rename"
    if "记住" in text or "以后请" in text:
        return "remember"
    if any(token in text for token in ("我们约定", "记得提醒我", "约好了")):
        return "agreement"
    if classify_live_need(text) is not None:
        return "chat"
    if any(
        token in text
        for token in (
            "讲个笑话",
            "讲笑话",
            "说个笑话",
            "说笑话",
            "来个笑话",
            "讲个段子",
            "说个段子",
            "来个段子",
        )
    ):
        return "joke"
    if any(token in text for token in ("怎么", "为什么", "是什么", "帮我看看")):
        return "knowledge"
    if any(token in text for token in ("我教你", "定义是", "步骤是")):
        return "teaching"
    if any(token in text for token in ("好累", "问崩", "难受", "紧张", "崩溃")):
        return "venting"
    if any(token in text for token in ("哈哈", "开玩笑", "假如我是", "其实是")):
        return "joke"
    return "chat"


def plan_turn(
    *,
    status: StatusName,
    stage: StageName,
    hunger_band: LifeBand,
    energy_band: LifeBand,
    primary_label: str | None,
    traits: TraitBands,
    time_of_day: TimeOfDay,
    user_text: str = "",
    user_label: str | None = None,
    safety: bool = False,
    just_returned: bool = False,
    overlay: Overlay | None = None,
) -> TurnPlan:
    facts = overlay or Overlay()
    label = user_label or classify_user_text(user_text)
    no_comfort = facts.no_comfort or "别安慰我" in user_text
    venting = label == "venting"
    mode = _mode(
        status=status,
        hunger_band=hunger_band,
        energy_band=energy_band,
        primary_label=primary_label,
        user_label=label,
        safety=safety,
    )
    duty = _duty(
        mode=mode,
        primary_label=primary_label,
        venting=venting,
        just_returned=just_returned,
    )
    sentences, questions = _budget(
        mode=mode,
        stage=stage,
        primary_label=primary_label,
        traits=traits,
        time_of_day=time_of_day,
        no_comfort=no_comfort,
        venting=venting,
    )
    if label == "joke" and mode == "companion":
        sentences, questions = 6, 2
    return TurnPlan(
        mode=mode,
        sentence_cap=sentences,
        question_cap=questions,
        sting_cap=0,
        first_duty=duty,
        stage=stage,
        status=status,
        traits=traits,
    )


def assemble_instruction(
    plan: TurnPlan,
    *,
    hunger_band: LifeBand,
    energy_band: LifeBand,
    primary_label: str | None,
    overlay: Overlay,
    recall: tuple[RecallItem, ...],
    recent_turns: tuple[RecentTurn, ...],
    time_of_day: TimeOfDay,
    user_text: str,
) -> Instruction:
    visible = tuple(item for item in recall if item.active)
    shown = user_text if plan.mode != "safety" else "已省略"
    return Instruction(
        mode=plan.mode,
        sentence_cap=plan.sentence_cap,
        question_cap=plan.question_cap,
        sting_cap=plan.sting_cap,
        first_duty=plan.first_duty,
        status=plan.status,
        stage=plan.stage,
        hunger_band=hunger_band,
        energy_band=energy_band,
        primary_label=primary_label,
        trait_bands=plan.traits,
        overlay=overlay,
        recall=visible,
        recent_turns=recent_turns[-RECENT_CONTEXT_TURNS:],
        time_of_day=time_of_day,
        user_text=shown,
    )


_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_FAMILIARITY_CAP = {"whelp": 0, "formed": 1, "awake": 2}


def _local_now(now: datetime, timezone: str) -> datetime:
    from zoneinfo import ZoneInfo

    try:
        return now.astimezone(ZoneInfo(timezone))
    except Exception:
        return now.astimezone(ZoneInfo("Asia/Shanghai"))


def weekday_name(now: datetime, timezone: str) -> str:
    return _WEEKDAYS[_local_now(now, timezone).weekday()]


def calendar_date(now: datetime, timezone: str) -> str:
    local = _local_now(now, timezone)
    return f"{local.year:04d}-{local.month:02d}-{local.day:02d}"


_NOT_A_NAME = frozenset({"谁", "什么", "啥", "哪个", "哪位", "未名"})


def accepted_spirit_name(value: str | None) -> str | None:
    """Keep a name token the model returned. The user sentence is not inspected."""
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not 1 <= len(token) <= 8 or token in _NOT_A_NAME:
        return None
    for char in token:
        if char.isascii() and char.isalnum():
            continue
        if "\u4e00" <= char <= "\u9fff":
            continue
        return None
    return token


def render_instruction_pack(
    instruction: Instruction,
    *,
    weekday: str,
    evidence: str,
    familiarity: tuple[str, ...] = (),
    spirit_name: str = "未名",
    calendar_date: str = "",
) -> str:
    """The only variable context sent to the model. No scores, weather, or catchphrases."""
    overlay = instruction.overlay
    lines = [
        f"mode: {instruction.mode}",
        f"first_duty: {instruction.first_duty}",
        f"status: {instruction.status}",
        f"stage: {instruction.stage}",
        f"spirit_name: {spirit_name or '未名'}",
        f"address: {overlay.address or '无'}",
        f"forbidden_address: {', '.join(overlay.forbidden_addresses) or '无'}",
        f"blocked_topic: {', '.join(overlay.blocked_topics) or '无'}",
        "memories:",
    ]
    if instruction.recall:
        for item in instruction.recall:
            if not item.active:
                continue
            kind = item.memory_type or "memory"
            lines.append(f"- {item.id} [{kind}] {item.summary}")
    else:
        lines.append("- 无")
    cap = _FAMILIARITY_CAP.get(instruction.stage, 0)
    if cap and familiarity:
        lines.append("familiarity:")
        for item in familiarity[:cap]:
            lines.append(f"- {item}")
    lines.append("recent:")
    if instruction.recent_turns:
        for turn in instruction.recent_turns:
            lines.append(f"- {turn.role}: {turn.text}")
    else:
        lines.append("- 无")
    lines.append(f"time_of_day: {instruction.time_of_day}")
    lines.append(f"date: {calendar_date}")
    lines.append(f"weekday: {weekday}")
    lines.append(f"evidence: {evidence}")
    lines.append(f"user: {instruction.user_text}")
    return "\n".join(lines)


def _mode(
    *,
    status: StatusName,
    hunger_band: LifeBand,
    energy_band: LifeBand,
    primary_label: str | None,
    user_label: str,
    safety: bool,
) -> ChatModeName:
    _ = (hunger_band, energy_band, primary_label)
    if safety or user_label == "safety":
        return "safety"
    if status == "lost":
        return "recall_only"
    if status in {"study", "away"}:
        return "away_note"
    if user_label == "knowledge":
        return "knowledge"
    return "companion"


def _duty(
    *,
    mode: ChatModeName,
    primary_label: str | None,
    venting: bool,
    just_returned: bool,
) -> FirstDuty:
    if mode == "safety":
        return "fixed_help"
    if mode == "recall_only":
        return "guide_food"
    if mode == "away_note":
        return "state_whereabouts"
    _ = (primary_label, venting)
    if mode == "knowledge" and not just_returned:
        return "answer_evidence"
    if just_returned:
        return "mention_leaving"
    return "follow_emotion"


def _budget(
    *,
    mode: ChatModeName,
    stage: StageName,
    primary_label: str | None,
    traits: TraitBands,
    time_of_day: TimeOfDay,
    no_comfort: bool,
    venting: bool,
) -> tuple[int, int]:
    _ = (stage, primary_label, traits, time_of_day)
    mode_sentences, mode_questions = _mode_caps(mode)
    if mode == "safety":
        return mode_sentences, mode_questions
    questions = mode_questions
    if venting:
        questions = min(questions, 1)
    if no_comfort:
        questions = 0
    return mode_sentences, questions


def _mode_caps(mode: ChatModeName) -> tuple[int, int]:
    if mode == "knowledge":
        return 6, 1
    if mode == "companion":
        return 3, 1
    if mode == "recall_only":
        return 1, 1
    if mode == "safety":
        return 1, 1
    return 1, 0


def _sting_cap(primary_label: str | None, traits: TraitBands) -> int:
    emotion = _emotion(primary_label)
    if emotion == "low":
        return 0
    if emotion == "irritable":
        return 1
    if traits.sharpness == "high":
        return 1
    return 0


_SETTLED_ALIASES = {
    "satisfied": "content",
    "irritated": "irritable",
}


def trait_level(score: int) -> TraitLevel:
    if score <= 39:
        return "low"
    if score >= 70:
        return "high"
    return "mid"


def plan_settled_turn(
    *,
    status: str,
    stage: str,
    hunger_band: str,
    energy_band: str,
    primary_label: str | None,
    closeness: int,
    curiosity: int,
    sharpness: int,
    nocturnal: int,
    stubborn: int,
    now: datetime,
    timezone: str,
    user_text: str,
    overlay: Overlay | None = None,
) -> TurnPlan:
    """One plan from status and the user's words. Scores do not change the mode."""
    return plan_turn(
        status=_status_name(status),
        stage=_stage_name(stage),
        hunger_band=_life_band(hunger_band),
        energy_band=_life_band(energy_band),
        primary_label=primary_label,
        traits=TraitBands(
            closeness=trait_level(closeness),
            curiosity=trait_level(curiosity),
            sharpness=trait_level(sharpness),
            nocturnal=trait_level(nocturnal),
            stubborn=trait_level(stubborn),
        ),
        time_of_day=_time_of_day(now, timezone),
        user_text=user_text,
        overlay=overlay,
    )


def _status_name(value: str) -> StatusName:
    if value in {"home", "away", "study", "lost"}:
        return value  # type: ignore[return-value]
    return "home"


def _stage_name(value: str) -> StageName:
    if value in {"whelp", "formed", "awake"}:
        return value  # type: ignore[return-value]
    return "whelp"


def _life_band(value: str) -> LifeBand:
    if value == "L4":
        return "L3"
    if value in {"L0", "L1", "L2", "L3"}:
        return value  # type: ignore[return-value]
    return "L2"


def _time_of_day(now: datetime, timezone: str) -> TimeOfDay:
    try:
        hour = now.astimezone(ZoneInfo(timezone)).hour
    except Exception:
        hour = now.astimezone(ZoneInfo("Asia/Shanghai")).hour
    if hour >= 23 or hour < 8:
        return "late_night"
    return "day"


def _emotion(primary_label: str | None) -> str:
    if primary_label is None:
        return ""
    named = normalize_emotion_label(primary_label)
    mapped = _LABELS.get(named, named)
    return _SETTLED_ALIASES.get(mapped, mapped)
