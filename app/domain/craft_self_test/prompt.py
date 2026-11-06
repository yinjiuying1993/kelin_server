"""Self-test prompt. It is not the room chat prompt."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

DialogueRole = Literal["user", "spirit"]

_INSTRUCTION = (
    "你正在帮主人试一项已经生成的技能。"
    "只根据技能快照和主人给出的对话回答。"
    "不要使用私人房间里的记忆，不要加分，不要把技能标识写进回复。"
    "只返回一个 JSON 对象，键只有 reply。"
)


def render_self_test_prompt(
    *,
    snapshot_body: str,
    dialogue: Sequence[tuple[DialogueRole, str]],
    text: str,
) -> tuple[str, str]:
    lines = [_INSTRUCTION, "技能快照：", snapshot_body, "已有对话："]
    for role, line in dialogue:
        cleaned = line.strip()
        if cleaned:
            lines.append(f"{role}: {cleaned}")
    lines.append("本轮：")
    lines.append(text)
    return "\n".join(lines), text
