from __future__ import annotations

import inspect
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.domain.chat import (
    ONBOARDING_STUB_REPLIES,
    OnboardingStubGenerator,
    chat_turn_request_hash,
    conversation_window_status,
    next_onboarding_step,
    next_ordinary_dialogue_rounds,
    should_extract_for_window,
)
from app.schemas.chat import ChatRequest
from app.services.chat import _replay_generation_source, settle_chat_turn
from pydantic import SecretStr, ValidationError


def test_step_does_not_advance_without_complete_pair() -> None:
    assert next_onboarding_step(0, onboarding=True, pair_complete=False) == 0
    assert next_onboarding_step(4, onboarding=True, pair_complete=False) == 4


def test_complete_onboarding_pair_advances_until_five() -> None:
    assert next_onboarding_step(0, onboarding=True, pair_complete=True) == 1
    assert next_onboarding_step(4, onboarding=True, pair_complete=True) == 5
    assert next_onboarding_step(5, onboarding=True, pair_complete=True) == 5


def test_onboarding_pair_does_not_increment_ordinary_rounds() -> None:
    assert next_ordinary_dialogue_rounds(0, onboarding=True, pair_complete=True) == 0
    assert next_ordinary_dialogue_rounds(3, onboarding=True, pair_complete=False) == 3


def test_ordinary_complete_pair_increments_ordinary_rounds() -> None:
    assert next_ordinary_dialogue_rounds(0, onboarding=False, pair_complete=True) == 1
    assert next_ordinary_dialogue_rounds(0, onboarding=False, pair_complete=False) == 0


def test_ordinary_window_ready_on_third_round_only() -> None:
    assert conversation_window_status(onboarding=False, user_round_count=1) == "open"
    assert conversation_window_status(onboarding=False, user_round_count=2) == "open"
    assert conversation_window_status(onboarding=False, user_round_count=3) == "ready"
    assert should_extract_for_window(onboarding=False, user_round_count=1) is False
    assert should_extract_for_window(onboarding=False, user_round_count=2) is False
    assert should_extract_for_window(onboarding=False, user_round_count=3) is True


def test_onboarding_window_never_becomes_ready() -> None:
    assert conversation_window_status(onboarding=True, user_round_count=3) == "open"
    assert should_extract_for_window(onboarding=True, user_round_count=3) is False
    assert should_extract_for_window(onboarding=True, user_round_count=5) is False


def test_stub_replies_are_deterministic_and_do_not_echo_user_text() -> None:
    generator = OnboardingStubGenerator()
    first = generator.generate(onboarding=True, onboarding_step=0)
    again = generator.generate(onboarding=True, onboarding_step=0)
    assert first.content == again.content == ONBOARDING_STUB_REPLIES[0]
    assert first.generation_source == "stub"
    assert first.input_units == 0
    assert "你好" not in first.content
    fifth = generator.generate(onboarding=True, onboarding_step=4)
    assert fifth.content == ONBOARDING_STUB_REPLIES[4]


def test_chat_turn_hash_ignores_client_id_and_changes_with_content() -> None:
    context = {
        "timezone": "Asia/Shanghai",
        "local_hour": 21,
        "weather": "cloudy",
        "city": None,
    }
    first = ChatRequest.model_validate(
        {
            "client_message_id": str(uuid4()),
            "content": "你好",
            "source": "text",
            "onboarding": True,
            "context": context,
        }
    )
    second = ChatRequest.model_validate(
        {
            "client_message_id": str(uuid4()),
            "content": "你好",
            "source": "text",
            "onboarding": True,
            "context": {**context, "local_hour": 8},
        }
    )
    changed = ChatRequest.model_validate(
        {
            "client_message_id": str(first.client_message_id),
            "content": "换一句",
            "source": "text",
            "onboarding": True,
            "context": context,
        }
    )
    assert chat_turn_request_hash(first) == chat_turn_request_hash(second)
    assert chat_turn_request_hash(first) != chat_turn_request_hash(changed)
    assert len(chat_turn_request_hash(first)) == 64


def test_settle_command_does_not_accept_user_id() -> None:
    payload = {
        "client_message_id": str(uuid4()),
        "content": "你好",
        "source": "text",
        "onboarding": True,
        "context": {
            "timezone": "Asia/Shanghai",
            "local_hour": 21,
            "weather": "cloudy",
            "city": None,
        },
        "user_id": str(uuid4()),
    }
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(payload)
    assert "user_id" not in inspect.signature(settle_chat_turn).parameters


def test_chat_request_rejects_client_window_bounds() -> None:
    base = {
        "client_message_id": str(uuid4()),
        "content": "你好",
        "source": "text",
        "onboarding": False,
        "context": {
            "timezone": "Asia/Shanghai",
            "local_hour": 21,
            "weather": "cloudy",
            "city": None,
        },
    }
    for field in ("start_message_id", "end_message_id", "conversation_window_id"):
        payload = {**base, field: str(uuid4())}
        with pytest.raises(ValidationError):
            ChatRequest.model_validate(payload)


def _request(*, onboarding: bool, content: str = "你好") -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "client_message_id": str(uuid4()),
            "content": content,
            "source": "text",
            "onboarding": onboarding,
            "context": {
                "timezone": "Asia/Shanghai",
                "local_hour": 21,
                "weather": "cloudy",
                "city": None,
            },
        }
    )


def test_replay_source_follows_factory_for_onboarding_and_ordinary() -> None:
    assert _replay_generation_source(_request(onboarding=True), None) == "stub"
    assert _replay_generation_source(_request(onboarding=False), None) == "stub"
    live = Settings(
        app_env="test",
        bailian_api_key=SecretStr("test-only-key"),
        bailian_chat_model="chat",
        bailian_app_id="test-app",
    )
    assert _replay_generation_source(_request(onboarding=True), live) == "provider"
    assert _replay_generation_source(_request(onboarding=False), live) == "provider"
