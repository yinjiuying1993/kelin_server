"""Versioned built-in interview bank. Spec §§4.5, 16.3."""

from __future__ import annotations

import uuid

from app.schemas.jsonb import SourceRef

BUILTIN_BANK_VERSION = "v1"
_NAMESPACE = uuid.UUID("7c4e1f2a-9b0d-4a6e-8c11-2f3d9a7b5e01")

_ENTRIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("self_intro", ("自我介绍", "介绍一下你自己")),
    ("strengths", ("优点", "缺点", "优缺点")),
    ("project", ("项目经历", "负责过什么项目")),
    ("conflict", ("冲突处理", "和同事意见不合")),
    ("star", ("star", "情景", "任务", "行动", "结果")),
)


def match_builtin_bank(query: str) -> tuple[SourceRef, ...]:
    lowered = query.casefold()
    for slug, keywords in _ENTRIES:
        if any(keyword.casefold() in lowered for keyword in keywords):
            return (
                SourceRef(
                    type="builtin_bank",
                    id=uuid.uuid5(_NAMESPACE, f"{BUILTIN_BANK_VERSION}:{slug}"),
                ),
            )
    return ()
