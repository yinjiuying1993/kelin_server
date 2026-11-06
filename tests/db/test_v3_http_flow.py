"""Run the craft and ask HTTP routes on the disposable database.

The model is the in-process stub. It returns the skill ids it was shown and a
fixed reply. This does not call a live model and does not sign the stage.
"""

from __future__ import annotations

import asyncio
import secrets
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.main import create_app
from app.providers.stub import ControllableProviderStub
from app.services.craft_ledger.worker import process_due_rating_settlements
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.db.harness import upgrade_empty_kelin_test_to_head

_INVITE = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_ACTOR: ContextVar[UUID] = ContextVar("v3_http_actor")
_ENGINE = None
_FACTORY: async_sessionmaker[AsyncSession] | None = None


def setup_module() -> None:
    global _ENGINE, _FACTORY
    url = upgrade_empty_kelin_test_to_head()
    _ENGINE = create_runtime_engine(url)
    _FACTORY = create_session_factory(_ENGINE)


def teardown_module() -> None:
    if _ENGINE is not None:
        asyncio.run(_ENGINE.dispose())


def _factory() -> async_sessionmaker[AsyncSession]:
    if _FACTORY is None:
        raise RuntimeError("test database was not prepared")
    return _FACTORY


def _invite() -> str:
    return "".join(secrets.choice(_INVITE) for _ in range(8))


async def _current_user() -> CurrentUser:
    return CurrentUser(id=_ACTOR.get())


async def _spirit(user_id: UUID, name: str) -> UUID:
    spirit_id = uuid4()
    async with claimed_transaction(_factory(), CurrentUser(id=user_id)) as session:
        await session.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, name, egg, invite_code"
                ") VALUES ("
                ":id, :user_id, :client_id, :name, 'wild', :invite)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid4(),
                "name": name,
                "invite": _invite(),
            },
        )
    return spirit_id


def _app():  # type: ignore[no-untyped-def]
    application = create_app(Settings(app_env="test"))
    application.state.session_factory = _factory()
    application.state.provider = ControllableProviderStub()
    application.dependency_overrides[require_current_user] = _current_user
    return application


def _ok(response):  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    return body["data"]


@pytest.mark.asyncio
async def test_http_routes_walk_the_craft_and_ask_flow() -> None:
    owner = uuid4()
    asker = uuid4()
    owner_spirit = await _spirit(owner, "雾生")
    await _spirit(asker, "石火")
    application = _app()
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _ACTOR.set(owner)
        empty = _ok(await client.get("/api/v1/craft/skills"))
        assert empty["items"] == []
        assert empty["total_points"] == 0

        create_id = uuid4()
        created = _ok(
            await client.post(
                "/api/v1/craft/skills",
                json={
                    "client_id": str(create_id),
                    "title": "泡茶",
                    "keywords": ["水温"],
                    "description": "先烧水，再闷三分钟。",
                    "reference_links": [],
                },
            )
        )
        craft_id = created["id"]
        assert created["title"] == "泡茶"
        assert created["visibility"] == "public"
        assert created["points"] == 0
        assert created["rank_name"] is None
        assert created["description"] == "先烧水，再闷三分钟。"
        replay = _ok(
            await client.post(
                "/api/v1/craft/skills",
                json={
                    "client_id": str(create_id),
                    "title": "泡茶",
                    "keywords": ["水温"],
                    "description": "先烧水，再闷三分钟。",
                    "reference_links": [],
                },
            )
        )
        assert replay["id"] == craft_id
        conflict = await client.post(
            "/api/v1/craft/skills",
            json={
                "client_id": str(create_id),
                "title": "泡好茶",
                "keywords": ["水温"],
                "description": "先烧水，再闷三分钟。",
                "reference_links": [],
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

        listed = _ok(await client.get("/api/v1/craft/skills"))
        assert listed["items"][0]["title"] == "泡茶"
        assert listed["total_points"] == 0
        detail = _ok(await client.get(f"/api/v1/craft/skills/{craft_id}"))
        assert detail["keywords"] == ["水温"]
        saved = _ok(
            await client.patch(
                f"/api/v1/craft/skills/{craft_id}",
                json={
                    "client_id": str(uuid4()),
                    "title": "泡好茶",
                    "keywords": ["水温"],
                    "description": "先烧水，再闷三分钟。",
                    "reference_links": [],
                },
            )
        )
        assert saved["id"] == craft_id
        assert saved["title"] == "泡好茶"

        ticket = _ok(await client.post(f"/api/v1/craft/skills/{craft_id}/self-test"))
        reply = _ok(
            await client.post(
                "/api/v1/craft/self-test/turns",
                json={"ticket": ticket["ticket"], "text": "水温呢"},
            )
        )
        assert reply["reply"] == "自测回复"
        bad_cursor = await client.get("/api/v1/craft/skills", params={"cursor": "nope"})
        assert bad_cursor.status_code == 422
        assert bad_cursor.json()["error"]["code"] == "INVALID_CURSOR"

        _ACTOR.set(asker)
        asked = _ok(
            await client.post(
                "/api/v1/ask/questions",
                json={"client_id": str(uuid4()), "text": "茶叶用多少度的水"},
            )
        )
        assert asked["matched"] is True
        question = asked["question"]
        question_id = question["id"]
        assert question["spirit_name"] == "雾生"
        assert question["spirit_id"] == str(owner_spirit)
        assert question["skill_title"] == "泡好茶"
        assert question["turns"][1]["text"] == "问艺回复"
        recent = _ok(await client.get("/api/v1/ask/questions/recent"))
        assert [item["id"] for item in recent["items"]] == [question_id]
        assert recent["items"][0]["skill_title"] == "泡好茶"
        assert recent["items"][0]["spirit_id"] == str(owner_spirit)
        page = _ok(await client.get("/api/v1/ask/questions", params={"view": "unrated"}))
        assert page["items"][0]["rated"] is False
        assert page["items"][0]["skill_title"] == "泡好茶"
        assert page["items"][0]["spirit_id"] == str(owner_spirit)
        assert "turns" not in page["items"][0]
        loaded = _ok(await client.get(f"/api/v1/ask/questions/{question_id}"))
        assert loaded["question"] == "茶叶用多少度的水"
        assert loaded["skill_title"] == "泡好茶"
        assert loaded["spirit_id"] == str(owner_spirit)
        followed = _ok(
            await client.post(
                f"/api/v1/ask/questions/{question_id}/turns",
                json={"client_id": str(uuid4()), "text": "闷多久"},
            )
        )
        assert followed["reply"] == "问艺回复"
        rated = _ok(
            await client.post(
                f"/api/v1/ask/questions/{question_id}/rating",
                json={"client_id": str(uuid4()), "rating": "满意"},
            )
        )
        assert rated["rating"] == "满意"
        again = _ok(
            await client.post(
                f"/api/v1/ask/questions/{question_id}/rating",
                json={"client_id": str(uuid4()), "rating": "非常满意"},
            )
        )
        assert again["rating"] == "满意"

        settled = await process_due_rating_settlements(_factory(), now=datetime.now(UTC))
        assert len(settled) == 1

        _ACTOR.set(owner)
        after = _ok(await client.get("/api/v1/craft/skills"))
        assert after["total_points"] == 5
        assert after["items"][0]["points"] == 5
        assert after["items"][0]["rank_name"] == "门边学徒"
        redeem = _ok(await client.get("/api/v1/craft/redeem"))
        assert redeem == {"eligible": False, "total_points": 5, "redeemed": False}
        helped = _ok(await client.get("/api/v1/ask/helped"))
        assert helped["item"] == {"spirit_name": "雾生", "help_count": 1}

        _ACTOR.set(asker)
        closed = await client.post(
            f"/api/v1/ask/questions/{question_id}/turns",
            json={"client_id": str(uuid4()), "text": "还能再问吗"},
        )
        assert closed.status_code == 409
        assert closed.json()["error"]["code"] == "ASK_CLOSED"
        profile = _ok(await client.get(f"/api/v1/spirits/{question['spirit_id']}/public"))
        assert profile["name"] == "雾生"
        assert profile["help_count"] == 1
        assert profile["skills"][0]["title"] == "泡好茶"
        assert "keywords" not in profile["skills"][0]

        _ACTOR.set(owner)
        hidden = _ok(
            await client.post(
                f"/api/v1/craft/skills/{craft_id}/visibility",
                json={"client_id": str(uuid4()), "visibility": "private"},
            )
        )
        assert hidden["visibility"] == "private"
        _ACTOR.set(asker)
        unmatched = _ok(
            await client.post(
                "/api/v1/ask/questions",
                json={"client_id": str(uuid4()), "text": "再问一次水温"},
            )
        )
        assert unmatched == {"matched": False, "question": None}

        _ACTOR.set(owner)
        removed = _ok(
            await client.request(
                "DELETE",
                f"/api/v1/craft/skills/{craft_id}",
                json={"client_id": str(uuid4())},
            )
        )
        assert removed == {"id": craft_id, "deleted": True}
        assert _ok(await client.get("/api/v1/craft/skills"))["items"] == []
        _ACTOR.set(asker)
        quiet = _ok(await client.get(f"/api/v1/spirits/{owner_spirit}/public"))
        assert quiet["skills"] == []
        assert quiet["help_count"] == 1
        kept = _ok(await client.get(f"/api/v1/ask/questions/{question_id}"))
        assert kept["skill_title"] == "泡好茶"

    async with claimed_transaction(_factory(), CurrentUser(id=owner)) as session:
        slug = await session.scalar(
            text("SELECT skill_slug FROM public.craft_skills WHERE id = :id"),
            {"id": craft_id},
        )
        asked_by_owner = await session.scalar(
            text("SELECT count(*) FROM public.ask_questions WHERE asker_user_id = :id"),
            {"id": owner},
        )
    assert isinstance(slug, str) and slug
    assert slug not in created["description"]
    assert "skill_slug" not in replay
    assert asked_by_owner == 0
