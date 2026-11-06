"""Ask-answer prompt. It is not the room chat prompt or the match prompt."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

DialogueRole = Literal["user", "spirit"]

_INSTRUCTION = (
    "你正在用一门已经抄下的技能回答提问。"
    "只根据详细描述、参考链接、原问题、已有对话和这一轮发言回答。"
    "不要补写用户没写的做法。"
    "这门本事覆盖不到时，说明接不住。"
    "医疗、法律、投资类问题只说明情况，不给结论式操作建议。"
    "不要使用私人记忆，不要加帮助次数，不要把技能标识写进回复。"
    "只返回一个 JSON 对象，键只有 reply。"
)


def render_ask_prompt(
    *,
    detail: str,
    reference_links: Sequence[str],
    question: str,
    dialogue: Sequence[tuple[DialogueRole, str]],
    text: str,
) -> tuple[str, str]:
    lines = [_INSTRUCTION, "详细描述：", detail, "参考链接："]
    lines.extend(link for link in reference_links if link.strip())
    lines.append("原问题：")
    lines.append(question)
    lines.append("已有对话：")
    for role, line in dialogue:
        cleaned = line.strip()
        if cleaned:
            lines.append(f"{role}: {cleaned}")
    lines.append("本轮：")
    lines.append(text)
    return "\n".join(lines), text
