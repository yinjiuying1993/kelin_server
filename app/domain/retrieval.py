"""Chat retrieval order: memory → pact/notes → builtin bank → remote search. Spec §16.3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.schemas.jsonb import SourceRef

RetrievalKind = Literal["local", "search", "unknown", "none"]


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    kind: RetrievalKind
    source_refs: tuple[SourceRef, ...] = ()


def plan_retrieval(
    *,
    memory_refs: tuple[SourceRef, ...],
    pact_id: UUID | None,
    builtin_refs: tuple[SourceRef, ...],
    search_query: str | None,
    remote_search_on: bool,
    onboarding: bool,
) -> RetrievalPlan:
    if memory_refs:
        return RetrievalPlan("local", memory_refs)
    if search_query is None or onboarding:
        return RetrievalPlan("none", ())
    if pact_id is not None:
        return RetrievalPlan("local", (SourceRef(type="pact", id=pact_id),))
    if builtin_refs:
        return RetrievalPlan("local", builtin_refs)
    if not remote_search_on:
        return RetrievalPlan("unknown", ())
    return RetrievalPlan("search", ())
