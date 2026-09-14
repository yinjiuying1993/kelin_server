from __future__ import annotations

from uuid import uuid4

import pytest
from app.providers.chat_schema import require_chat_output, verified_memory_ids
from app.providers.errors import ProviderError
from app.providers.types import ChatCitation, ChatOutput
from pydantic import ValidationError


def test_require_chat_output_rejects_natural_language_extra_and_enums() -> None:
    with pytest.raises(ProviderError) as natural:
        require_chat_output("知道了，阿年。")
    assert natural.value.code == "MODEL_UNAVAILABLE"
    with pytest.raises(ProviderError):
        require_chat_output({"reply": "嗯。", "extra": True})
    with pytest.raises(ProviderError):
        require_chat_output({"reply": "嗯。", "safety": "block"})
    with pytest.raises(ProviderError):
        require_chat_output({"reply": "嗯。", "intent": "extract"})
    accepted = require_chat_output(ChatOutput(reply="嗯。"))
    assert accepted.reply == "嗯。"
    assert accepted.citations == []


def test_citation_schema_is_memory_id_only() -> None:
    memory_id = uuid4()
    output = ChatOutput.model_validate(
        {
            "reply": "嗯。",
            "citations": [{"type": "memory", "id": str(memory_id)}],
        }
    )
    assert output.citations[0].id == memory_id
    with pytest.raises(ValidationError):
        ChatCitation.model_validate({"type": "pact", "id": str(memory_id)})
    with pytest.raises(ValidationError):
        ChatCitation.model_validate({"type": "memory", "id": str(memory_id), "content": "秘密"})


def test_unverified_or_foreign_citation_is_rejected() -> None:
    owned = uuid4()
    foreign = uuid4()
    assert verified_memory_ids([], allowed_ids=frozenset({owned})) == ()
    assert verified_memory_ids(
        [ChatCitation(type="memory", id=owned)],
        allowed_ids=frozenset({owned}),
    ) == (owned,)
    with pytest.raises(ProviderError) as missing:
        verified_memory_ids(
            [ChatCitation(type="memory", id=foreign)],
            allowed_ids=frozenset({owned}),
        )
    assert missing.value.code == "MODEL_UNAVAILABLE"
    with pytest.raises(ProviderError):
        verified_memory_ids(
            [ChatCitation(type="memory", id=owned)],
            allowed_ids=frozenset(),
        )
