from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.logging import redact_event_dict
from app.providers.contract import (
    CLIENT_CHAT_TIMEOUT_SECONDS,
    PROVIDER_CONDITIONAL_RETRY_COUNT,
    PROVIDER_RETRY_ON,
    PROVIDER_TIMEOUT_SECONDS,
    live_provider_readiness,
    map_provider_failure,
    model_alias,
)
from app.providers.types import ChatOutput, ExtractInput, ExtractOutput
from pydantic import SecretStr, ValidationError


def test_timeouts_match_spec_and_stay_below_client_chat_budget() -> None:
    assert PROVIDER_TIMEOUT_SECONDS["chat"] == 18
    assert PROVIDER_TIMEOUT_SECONDS["extract"] == 30
    assert PROVIDER_TIMEOUT_SECONDS["asr"] == 22
    assert PROVIDER_TIMEOUT_SECONDS["tts"] == 18
    assert PROVIDER_TIMEOUT_SECONDS["vision"] == 28
    assert PROVIDER_TIMEOUT_SECONDS["safety"] == 28
    assert PROVIDER_TIMEOUT_SECONDS["search"] == 12
    assert PROVIDER_TIMEOUT_SECONDS["chat"] < CLIENT_CHAT_TIMEOUT_SECONDS
    assert (
        PROVIDER_TIMEOUT_SECONDS["chat"] + PROVIDER_TIMEOUT_SECONDS["tts"]
        < CLIENT_CHAT_TIMEOUT_SECONDS
    )
    assert CLIENT_CHAT_TIMEOUT_SECONDS == 48
    assert PROVIDER_CONDITIONAL_RETRY_COUNT == 1
    assert PROVIDER_RETRY_ON == frozenset({"connect", "http_429", "http_5xx"})


def test_provider_failure_mapping() -> None:
    timeout = map_provider_failure("timeout")
    assert timeout.code == "PROVIDER_TIMEOUT"
    assert timeout.status_code == 504
    assert timeout.retryable is True
    unavailable = map_provider_failure("unavailable")
    assert unavailable.code == "MODEL_UNAVAILABLE"
    assert unavailable.status_code == 503
    rate = map_provider_failure("http_429")
    assert rate.code == "RATE_LIMITED"
    assert rate.status_code == 429
    quota = map_provider_failure("quota_exceeded")
    assert quota.code == "QUOTA_EXCEEDED"
    assert quota.retryable is False


def test_missing_secret_and_alias_block_live_provider() -> None:
    settings = Settings(app_env="test")
    ready = live_provider_readiness(settings)
    assert ready.blocked is True
    assert ready.chat is False
    assert ready.extract is False
    assert ready.search is False
    assert ready.vision is False
    assert ready.safety is False
    assert "BAILIAN_API_KEY" in ready.missing
    assert "BAILIAN_CHAT_MODEL" in ready.missing
    dumped = str(ready)
    assert "sk-" not in dumped


def test_live_chat_ready_only_with_key_and_chat_alias() -> None:
    secret = "test-only-bailian-key"
    settings = Settings(
        app_env="dev",
        bailian_api_key=SecretStr(secret),
        bailian_chat_model="chat",
        bailian_extract_model="extract",
    )
    ready = live_provider_readiness(settings)
    assert ready.blocked is False
    assert ready.chat is True
    assert ready.extract is True
    assert ready.search is False
    assert model_alias(settings, "chat") == "chat"
    presence = settings.config_presence()
    dumped = str(presence) + str(ready)
    assert presence["bailian_api_key"] == "set"
    assert presence["bailian_chat_model"] == "set"
    assert secret not in dumped
    assert settings.bailian_api_key is not None
    assert settings.bailian_api_key.get_secret_value() == secret


def test_search_stays_internal_and_needs_explicit_enable() -> None:
    settings = Settings(
        app_env="dev",
        bailian_api_key=SecretStr("test-only-bailian-key"),
        bailian_chat_model="chat",
        bailian_search_enabled=True,
    )
    ready = live_provider_readiness(settings)
    assert ready.search is False
    assert "BAILIAN_SEARCH_MODEL" in ready.missing
    enabled = Settings(
        app_env="dev",
        bailian_api_key=SecretStr("test-only-bailian-key"),
        bailian_chat_model="chat",
        bailian_search_model="search",
        bailian_search_enabled=True,
    )
    assert live_provider_readiness(enabled).search is True


def test_chat_and_extract_output_forbid_extra_and_message_ids() -> None:
    ChatOutput.model_validate({"reply": "嗯。"})
    with pytest.raises(ValidationError):
        ChatOutput.model_validate({"reply": "嗯。", "model": "qwen-plus"})
    with pytest.raises(ValidationError):
        ChatOutput.model_validate({"reply": "嗯。", "intent": "search"})
    with pytest.raises(ValidationError):
        ChatOutput.model_validate(
            {
                "reply": "嗯。",
                "citations": [{"type": "web", "id": str(uuid4())}],
            }
        )
    with pytest.raises(ValidationError):
        ChatOutput.model_validate(
            {
                "reply": "嗯。",
                "citations": [
                    {"type": "memory", "id": str(uuid4()), "summary": "秘密"},
                ],
            }
        )
    with pytest.raises(ValidationError):
        ExtractOutput.model_validate(
            {
                "memories": [
                    {
                        "type": "secret",
                        "summary": "x",
                        "tags": [],
                        "salience": 1,
                        "confidence": 0.9,
                    }
                ]
            }
        )
    with pytest.raises(ValidationError):
        ExtractOutput.model_validate(
            {
                "memories": [
                    {
                        "type": "preference",
                        "summary": "x",
                        "tags": [],
                        "salience": 1,
                        "confidence": 0.9,
                        "personality_delta": {"dimension": "love", "value": 1},
                    }
                ]
            }
        )
    ExtractOutput.model_validate({"memories": [], "style_samples": []})
    with pytest.raises(ValidationError):
        ExtractOutput.model_validate({"style_samples": [{}]})
    with pytest.raises(ValidationError):
        ExtractInput.model_validate(
            {
                "conversation_window_id": str(uuid4()),
                "start_message_id": str(uuid4()),
            }
        )
    with pytest.raises(ValidationError):
        ExtractOutput.model_validate(
            {
                "memories": [
                    {
                        "type": "preference",
                        "summary": "x",
                        "tags": [],
                        "salience": 1,
                        "confidence": 0.9,
                        "extra": True,
                    }
                ]
            }
        )


def test_config_and_env_example_do_not_hardcode_a_secret() -> None:
    config = Path(__file__).resolve().parents[2] / "app" / "core" / "config.py"
    example = Path(__file__).resolve().parents[2] / ".env.example"
    config_text = config.read_text(encoding="utf-8")
    example_text = example.read_text(encoding="utf-8")
    assert "sk-" not in config_text
    assert "BAILIAN_API_KEY=" in example_text
    assert "BAILIAN_APP_ID=" in example_text
    assert "BAILIAN_TTS_VOICE=" in example_text
    assert "sk-" not in example_text
    assert "qwen" not in example_text.lower()


def test_bailian_secret_is_redacted_in_logs() -> None:
    event = {"bailian_api_key": "test-only-bailian-key", "route": "/health/ready"}
    out = redact_event_dict(event)
    assert out["bailian_api_key"] == "[redacted]"
    assert "test-only-bailian-key" not in str(out)
