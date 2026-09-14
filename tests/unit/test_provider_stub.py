from __future__ import annotations

import ast
import asyncio
from dataclasses import asdict
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.logging import configure_logging
from app.domain.chat import ONBOARDING_STUB_REPLIES
from app.providers.errors import ProviderCancelled, ProviderError, ProviderStubUnsupported
from app.providers.stub import STUB_LABEL, STUB_SOURCE, ControllableProviderStub
from app.providers.types import (
    ChatInput,
    ChatOutput,
    ExtractInput,
    ExtractTurn,
    ProviderCallRecord,
    SearchInput,
)
from pydantic import ValidationError

SECRET_CONTENT = "UNIQUE_STUB_BODY_DO_NOT_LOG"


def _input(*, step: int = 0, content: str = SECRET_CONTENT) -> ChatInput:
    return ChatInput(onboarding=True, onboarding_step=step, content=content)


def test_stub_identity_is_not_a_live_provider() -> None:
    stub = ControllableProviderStub()
    assert stub.source == STUB_SOURCE == "stub"
    assert "stub" in stub.label
    assert "live" in stub.label
    assert "Bailian" not in type(stub).__name__
    assert STUB_LABEL == stub.label


def test_chat_output_cannot_claim_live_provider() -> None:
    with pytest.raises(ValidationError):
        ChatOutput.model_validate({"reply": "嗯。", "generation_source": "provider"})
    with pytest.raises(ValidationError):
        ChatInput.model_validate(
            {
                "onboarding": True,
                "onboarding_step": 0,
                "content": "你好",
                "model": "qwen-plus",
            }
        )


def test_stub_module_does_not_import_live_sdks() -> None:
    path = Path(__file__).resolve().parents[2] / "app" / "providers" / "stub.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert "httpx" not in imported
    assert "dashscope" not in imported
    assert "openai" not in imported


@pytest.mark.asyncio
async def test_success_is_deterministic_and_does_not_echo_user_text() -> None:
    stub = ControllableProviderStub()
    first = await stub.chat(_input(step=0, content="请叫我宝宝"))
    again = await stub.chat(_input(step=0, content="请叫我宝宝"))
    assert first.reply == again.reply == ONBOARDING_STUB_REPLIES[0]
    assert first.reply != "请叫我宝宝"
    assert first.intent == "chat"
    assert first.citations == []
    assert first.safety == "allow"
    assert first.search_query is None
    assert stub.source == "stub"
    assert [record.mode for record in stub.calls] == ["success", "success"]
    assert [record.outcome for record in stub.calls] == ["success", "success"]
    assert all(record.source == "stub" for record in stub.calls)
    assert all(record.capability == "chat" for record in stub.calls)


@pytest.mark.asyncio
async def test_delay_error_cancel_and_fifo_script() -> None:
    stub = ControllableProviderStub()
    stub.enqueue("error")
    stub.enqueue("delay", latency_ms=40)
    stub.enqueue("cancel")
    stub.enqueue("success")

    with pytest.raises(ProviderError) as failed:
        await stub.chat(_input())
    assert failed.value.code == "MODEL_UNAVAILABLE"

    started = asyncio.get_running_loop().time()
    delayed = await stub.chat(_input(step=1))
    elapsed_ms = (asyncio.get_running_loop().time() - started) * 1000
    assert delayed.reply == ONBOARDING_STUB_REPLIES[1]
    assert elapsed_ms >= 30

    with pytest.raises(ProviderCancelled):
        await stub.chat(_input())

    last = await stub.chat(_input(step=2))
    assert last.reply == ONBOARDING_STUB_REPLIES[2]
    assert [record.mode for record in stub.calls] == ["error", "delay", "cancel", "success"]
    assert [record.outcome for record in stub.calls] == [
        "error",
        "success",
        "cancelled",
        "success",
    ]


@pytest.mark.asyncio
async def test_delay_task_cancel_is_recorded() -> None:
    stub = ControllableProviderStub()
    stub.enqueue("delay", latency_ms=2000)
    task = asyncio.create_task(stub.chat(_input()))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(stub.calls) == 1
    assert stub.calls[0].outcome == "cancelled"
    assert stub.calls[0].mode == "delay"
    assert stub.calls[0].source == "stub"


@pytest.mark.asyncio
async def test_call_records_and_logs_omit_content() -> None:
    stub = ControllableProviderStub()
    buf = StringIO()
    with patch("sys.stdout", buf):
        configure_logging(Settings(app_env="test"))
        await stub.chat(_input(content=SECRET_CONTENT))
    dumped = buf.getvalue()
    record = stub.calls[0]
    payload = asdict(record)
    assert SECRET_CONTENT not in dumped
    assert SECRET_CONTENT not in str(payload)
    assert "content" not in payload
    assert "reply" not in payload
    assert "prompt" not in payload
    assert record == ProviderCallRecord(
        capability="chat",
        source="stub",
        mode="success",
        outcome="success",
        latency_ms=record.latency_ms,
    )
    assert "provider_stub_chat" in dumped
    assert "stub" in dumped


@pytest.mark.asyncio
async def test_extract_stub_is_deterministic_and_other_capabilities_unsupported() -> None:
    stub = ControllableProviderStub()
    empty = await stub.extract(ExtractInput(conversation_window_id=uuid4()))
    assert empty.memories == []
    assert empty.style_samples == []
    window_id = uuid4()
    produced = await stub.extract(
        ExtractInput(
            conversation_window_id=window_id,
            turns=[ExtractTurn(role="user", content="希望叫我阿年")],
        )
    )
    assert len(produced.memories) == 1
    assert produced.memories[0].confidence == 0.96
    assert produced.memories[0].personality_delta is not None
    assert produced.memories[0].personality_delta.dimension == "closeness"
    assert len(produced.style_samples) == 1
    with pytest.raises(ProviderStubUnsupported):
        await stub.transcribe(object())
    with pytest.raises(ProviderStubUnsupported):
        await stub.synthesize(object())
    safety = await stub.moderate(object())
    vision = await stub.vision(object())
    assert safety.decision == "allow"
    assert vision.prop == "lamp"
    with pytest.raises(ProviderStubUnsupported):
        await stub.search(SearchInput(query="q"))
    assert stub.source == "stub"
    assert stub.calls == ()


def test_enqueue_delay_requires_latency() -> None:
    stub = ControllableProviderStub()
    with pytest.raises(ValueError, match="latency_ms"):
        stub.enqueue("delay", latency_ms=0)
