"""S5-T02: schema and ORM reject relation/speech/sight."""

from __future__ import annotations

from inspect import getsource
from typing import get_args
from uuid import uuid4

from app.models.engagement import Memory
from app.providers.types import ExtractMemoryDraft, MemoryType
from app.schemas.debug import DebugMemoryCreateRequest
from app.schemas.memory import MemoryPublic
from app.schemas.social import ReportMemorySnapshot
from pydantic import ValidationError
from pytest import raises

ILLEGAL = ("relation", "speech", "sight")
ALLOWED: tuple[MemoryType, ...] = ("preference", "knowledge", "emotion", "relationship")


def test_public_memory_type_alias_is_four_kinds() -> None:
    assert set(get_args(MemoryType)) == set(ALLOWED)


def test_report_snapshot_schema_only_allows_four_types() -> None:
    source = getsource(ReportMemorySnapshot)
    assert "MemoryType" in source
    assert "speech" not in source
    assert "sight" not in source
    assert "'relation'" not in source
    for kind in ALLOWED:
        ReportMemorySnapshot(id=uuid4(), type=kind, summary="雨夜路灯")
    for kind in ILLEGAL:
        with raises(ValidationError):
            ReportMemorySnapshot(id=uuid4(), type=kind, summary="非法")  # type: ignore[arg-type]


def test_extract_and_debug_schemas_reject_illegal_types() -> None:
    for kind in ALLOWED:
        ExtractMemoryDraft(
            type=kind,
            summary="合法候选",
            normalized_topic_key=f"{kind}:user:item:sample",
            source_quote="合法候选",
            signal="explicit",
            confidence=0.9,
        )
        DebugMemoryCreateRequest(type=kind, summary="合法候选", salience=80, confidence=0.9)
    for kind in ILLEGAL:
        with raises(ValidationError):
            ExtractMemoryDraft(
                type=kind,  # type: ignore[arg-type]
                summary="非法",
                normalized_topic_key="preference:user:item:sample",
                source_quote="非法",
                signal="explicit",
                confidence=0.9,
            )
        with raises(ValidationError):
            DebugMemoryCreateRequest(
                type=kind,  # type: ignore[arg-type]
                summary="非法",
                salience=80,
                confidence=0.9,
            )


def test_public_memory_models_reject_illegal_types() -> None:
    for kind in ILLEGAL:
        with raises(ValidationError):
            MemoryPublic.model_validate(
                {
                    "id": str(uuid4()),
                    "type": kind,
                    "summary": "非法",
                    "salience": 80,
                    "confidence": 0.9,
                    "status": "active",
                    "version": 1,
                    "created_at": "2026-09-20T04:00:00Z",
                }
            )


def test_orm_rejects_illegal_memory_type() -> None:
    for kind in ALLOWED:
        assert Memory(type=kind).type == kind
    for kind in ILLEGAL:
        with raises(ValueError, match="illegal memory type"):
            Memory(type=kind)
