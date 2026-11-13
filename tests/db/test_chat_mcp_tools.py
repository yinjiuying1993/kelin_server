from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.chat import UNKNOWN_SOURCE_REPLY
from app.providers.errors import ProviderError
from app.providers.stub import ControllableProviderStub
from app.providers.types import ChatInput, ChatOutput
from app.schemas.chat import ChatRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.chat import settle_chat_turn
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


class _McpProvider(ControllableProviderStub):
    def __init__(
        self,
        *,
        weather_text: str | None = (
            '{"city":"杭州市","forecasts":[{"date":"2026-10-21","week":"3",'
            '"dayweather":"晴","nightweather":"晴","daytemp":"18","nighttemp":"8",'
            '"daywind":"东","nightwind":"东","daypower":"1-3"}]}'
        ),
        news_text: str | None = "头条 https://news.example/a",
        weather_error: Exception | None = None,
        news_error: Exception | None = None,
    ) -> None:
        super().__init__()
        self._weather_text = weather_text
        self._news_text = news_text
        self._weather_error = weather_error
        self._news_error = news_error
        self.extend_calls: list[tuple[str, str | None, str | None]] = []
        self.chat_calls = 0

    async def chat(self, value: ChatInput) -> ChatOutput:
        self.chat_calls += 1
        mode = next(
            (
                line.split(": ", 1)[1].strip()
                for line in value.emotion_context.splitlines()
                if line.startswith("mode: ")
            ),
            "companion",
        )
        intent = mode if mode in {
            "safety",
            "recall_only",
            "away_note",
            "knowledge",
            "tired",
            "hungry",
            "companion",
        } else "companion"
        if "天气" in value.content:
            city = "杭州" if "杭州" in value.content else None
            if city is None:
                return ChatOutput(
                    reply="你在哪个城市？我帮你看天气。",
                    intent=intent,
                    tool="查询城市天气",
                )
            return ChatOutput(reply="我看看。", intent=intent, tool="查询城市天气", tool_arg=city)
        if "新闻" in value.content:
            return ChatOutput(reply="我看看。", intent=intent, tool="检索新闻", tool_arg=value.content)
        return ChatOutput(reply="嗯。", intent=intent)

    async def extend(
        self,
        *,
        query: str,
        argument: str | None = None,
        default_city: str | None = None,
        user_text: str | None = None,
    ) -> str:
        del user_text
        self.extend_calls.append((query, argument, default_city))
        if "新闻" in query:
            if self._news_error is not None:
                raise self._news_error
            assert self._news_text is not None
            return self._news_text
        if self._weather_error is not None:
            raise self._weather_error
        if not (argument or "").strip() and not default_city:
            raise ProviderError("TOOL_ARGUMENT_MISSING")
        assert self._weather_text is not None
        return self._weather_text


def _spirit_request() -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "egg": "warm",
            "name": "未名",
            "consents": {
                "ai_disclosure": {
                    "document_version": "2026-11-12",
                    "explicitly_accepted": True,
                },
                "data_notice": {"document_version": "2026-11-12", "displayed": True},
                "user_terms": {"document_version": "2026-11-12", "displayed": True},
            },
        }
    )


def _chat_request(content: str, *, city: str | None = None) -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "client_message_id": str(uuid.uuid4()),
            "content": content,
            "source": "text",
            "context": {
                "timezone": "Asia/Shanghai",
                "local_hour": 12,
                "weather": "cloudy",
                "city": city,
            },
        }
    )


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


def test_weather_and_news_mcp_facts_enter_the_reply() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_mcp_tools(url))


async def _assert_mcp_tools(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        assert created.hatched_at is not None
        await session.execute(
            text(
                "UPDATE public.user_preferences SET "
                "location_weather_on = true, remote_search_on = true, "
                "default_city = '杭州' WHERE user_id = :id"
            ),
            {"id": user_id},
        )

    provider = _McpProvider()
    async with claimed_transaction(factory, user) as session:
        settled = await settle_chat_turn(
            session, user, _chat_request("杭州天气"), now=NOW, provider=provider
        )
    assert settled.spirit_content == "杭州市今天晴，8到18℃，东风1-3级。"
    assert provider.chat_calls == 1
    assert provider.extend_calls[0][0] == "查询城市天气"
    assert provider.extend_calls[0][1] == "杭州"

    async with claimed_transaction(factory, user) as session:
        settled = await settle_chat_turn(
            session, user, _chat_request("今天天气怎么样"), now=NOW, provider=provider
        )
    assert settled.spirit_content == "杭州市今天晴，8到18℃，东风1-3级。"
    assert provider.extend_calls[-1][2] == "杭州"

    ask = _McpProvider()
    async with claimed_transaction(factory, user) as session:
        await session.execute(
            text("UPDATE public.user_preferences SET default_city = NULL WHERE user_id = :id"),
            {"id": user_id},
        )
        settled = await settle_chat_turn(
            session, user, _chat_request("今天天气怎么样"), now=NOW, provider=ask
        )
    assert settled.spirit_content == "你在哪个城市？我帮你看天气。"
    assert ask.extend_calls == []

    news = _McpProvider()
    async with claimed_transaction(factory, user) as session:
        settled = await settle_chat_turn(
            session, user, _chat_request("最近有什么新闻"), now=NOW, provider=news
        )
    assert settled.spirit_content == "1. **头条** [详情](https://news.example/a)"
    assert news.chat_calls == 1
    assert news.extend_calls[0][0] == "检索新闻"
    assert [ref.url for ref in settled.spirit_source_refs] == ["https://news.example/a"]

    failed = _McpProvider(weather_error=ProviderError("MODEL_UNAVAILABLE"))
    async with claimed_transaction(factory, user) as session:
        settled = await settle_chat_turn(
            session, user, _chat_request("杭州天气"), now=NOW, provider=failed
        )
    assert settled.spirit_content == UNKNOWN_SOURCE_REPLY
    await engine.dispose()
