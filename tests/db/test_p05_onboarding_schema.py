import asyncio
import uuid
from datetime import time

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import (
    HEAD_REVISION,
    upgrade_empty_kelin_test_to,
    upgrade_empty_kelin_test_to_head,
    upgrade_kelin_test_to,
)

PREVIOUS_HEAD = "20260908_0006"


async def _insert_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _expect_integrity(url: str, sql: str, params: dict[str, object]) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params)
    except IntegrityError:
        await engine.dispose()
        return
    await engine.dispose()
    raise AssertionError("expected IntegrityError")


async def _column_type(url: str, table: str, column: str) -> str:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table "
                "AND column_name = :column"
            ),
            {"table": table, "column": column},
        )
        value = row.scalar_one()
    await engine.dispose()
    return str(value)


async def _alembic_current(url: str) -> list[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT version_num FROM alembic_version"))
        versions = [str(item[0]) for item in rows.fetchall()]
    await engine.dispose()
    return versions


def test_preference_and_spirit_defaults_match_spec() -> None:
    url = upgrade_empty_kelin_test_to_head()
    user_id = uuid.uuid4()
    asyncio.run(_insert_user(url, user_id))

    async def _run() -> tuple[object, ...]:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO public.user_preferences (user_id) VALUES (:user_id)"),
                {"user_id": user_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO public.spirits ("
                    "user_id, client_id, egg, invite_code, "
                    "closeness, curiosity, sharpness, nocturnal, stubborn"
                    ") VALUES (:user_id, :client_id, 'wild', 'WYLD2347', 50, 50, 50, 50, 50)"
                ),
                {"user_id": user_id, "client_id": uuid.uuid4()},
            )
            pref = (
                await conn.execute(
                    text(
                        "SELECT tts_on, push_on, visit_on, dnd_start, dnd_end, timezone, "
                        "location_weather_on, remote_search_on, version, default_city "
                        "FROM public.user_preferences WHERE user_id = :user_id"
                    ),
                    {"user_id": user_id},
                )
            ).one()
            spirit = (
                await conn.execute(
                    text(
                        "SELECT onboarding_step, version, onboarding_completed_at, hatched_at "
                        "FROM public.spirits WHERE user_id = :user_id"
                    ),
                    {"user_id": user_id},
                )
            ).one()
        await engine.dispose()
        return (*pref, *spirit)

    (
        tts_on,
        push_on,
        visit_on,
        dnd_start,
        dnd_end,
        timezone,
        location_weather_on,
        remote_search_on,
        pref_version,
        default_city,
        onboarding_step,
        spirit_version,
        completed_at,
        hatched_at,
    ) = asyncio.run(_run())
    assert tts_on is False
    assert push_on is False
    assert visit_on is True
    assert dnd_start == time(23, 0)
    assert dnd_end == time(8, 0)
    assert timezone == "Asia/Shanghai"
    assert location_weather_on is False
    assert remote_search_on is True
    assert pref_version == 1
    assert default_city is None
    assert onboarding_step == 0
    assert spirit_version == 1
    assert completed_at is None
    assert hatched_at is None
    assert asyncio.run(_column_type(url, "spirits", "version")) == "bigint"
    assert asyncio.run(_column_type(url, "user_preferences", "version")) == "integer"


def test_consent_assertion_must_match_type_and_ai_disclosure_cannot_withdraw() -> None:
    url = upgrade_empty_kelin_test_to_head()
    user_id = uuid.uuid4()
    asyncio.run(_insert_user(url, user_id))

    async def _insert_valid() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO public.account_consents ("
                    "user_id, consent_type, document_version, client_id, assertion"
                    ") VALUES (:user_id, 'ai_disclosure', 'v1', :client_id, "
                    "'explicitly_accepted')"
                ),
                {"user_id": user_id, "client_id": uuid.uuid4()},
            )
            await conn.execute(
                text(
                    "INSERT INTO public.account_consents ("
                    "user_id, consent_type, document_version, client_id, assertion"
                    ") VALUES (:user_id, 'data_notice', 'v1', :client_id, 'displayed')"
                ),
                {"user_id": user_id, "client_id": uuid.uuid4()},
            )
            await conn.execute(
                text(
                    "INSERT INTO public.account_consents ("
                    "user_id, consent_type, document_version, client_id, assertion"
                    ") VALUES (:user_id, 'user_terms', 'v1', :client_id, 'displayed')"
                ),
                {"user_id": user_id, "client_id": uuid.uuid4()},
            )
        await engine.dispose()

    asyncio.run(_insert_valid())
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.account_consents ("
            "user_id, consent_type, document_version, client_id, assertion"
            ") VALUES (:user_id, 'ai_disclosure', 'v2', :client_id, 'displayed')",
            {"user_id": user_id, "client_id": uuid.uuid4()},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.account_consents ("
            "user_id, consent_type, document_version, client_id, assertion"
            ") VALUES (:user_id, 'user_terms', 'v2', :client_id, 'explicitly_accepted')",
            {"user_id": user_id, "client_id": uuid.uuid4()},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.account_consents ("
            "user_id, consent_type, document_version, client_id, assertion"
            ") VALUES (:user_id, 'data_notice', 'v2', :client_id, 'explicitly_accepted')",
            {"user_id": user_id, "client_id": uuid.uuid4()},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "UPDATE public.account_consents SET withdrawn_at = now() "
            "WHERE user_id = :user_id AND consent_type = 'ai_disclosure'",
            {"user_id": user_id},
        )
    )

    async def _withdraw_terms() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE public.account_consents SET withdrawn_at = now() "
                    "WHERE user_id = :user_id AND consent_type = 'user_terms'"
                ),
                {"user_id": user_id},
            )
        await engine.dispose()

    asyncio.run(_withdraw_terms())


def test_preference_timezone_city_and_version_checks() -> None:
    url = upgrade_empty_kelin_test_to_head()
    user_id = uuid.uuid4()
    asyncio.run(_insert_user(url, user_id))
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.user_preferences (user_id, timezone) VALUES (:user_id, '')",
            {"user_id": user_id},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.user_preferences (user_id, timezone) VALUES (:user_id, :timezone)",
            {"user_id": user_id, "timezone": "x" * 65},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.user_preferences (user_id, default_city) VALUES (:user_id, '')",
            {"user_id": user_id},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.user_preferences (user_id, version) VALUES (:user_id, 0)",
            {"user_id": user_id},
        )
    )

    async def _insert_ok() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO public.user_preferences "
                    "(user_id, timezone, default_city, version) "
                    "VALUES (:user_id, 'Asia/Shanghai', '上海', 1)"
                ),
                {"user_id": user_id},
            )
        await engine.dispose()

    asyncio.run(_insert_ok())


def test_upgrade_from_previous_head_backfills_assertion_and_widens_spirit_version() -> None:
    url = upgrade_empty_kelin_test_to(PREVIOUS_HEAD)
    user_id = uuid.uuid4()
    consent_ai = uuid.uuid4()
    consent_terms = uuid.uuid4()
    spirit_id = uuid.uuid4()

    async def _seed_at_previous_head() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
            await conn.execute(
                text(
                    "INSERT INTO public.account_consents ("
                    "id, user_id, consent_type, document_version, client_id"
                    ") VALUES (:id, :user_id, 'ai_disclosure', 'v1', :client_id)"
                ),
                {"id": consent_ai, "user_id": user_id, "client_id": uuid.uuid4()},
            )
            await conn.execute(
                text(
                    "INSERT INTO public.account_consents ("
                    "id, user_id, consent_type, document_version, client_id"
                    ") VALUES (:id, :user_id, 'user_terms', 'v1', :client_id)"
                ),
                {"id": consent_terms, "user_id": user_id, "client_id": uuid.uuid4()},
            )
            await conn.execute(
                text("INSERT INTO public.user_preferences (user_id) VALUES (:user_id)"),
                {"user_id": user_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO public.spirits ("
                    "id, user_id, client_id, egg, invite_code, "
                    "closeness, curiosity, sharpness, nocturnal, stubborn"
                    ") VALUES ("
                    ":id, :user_id, :client_id, 'warm', 'WARM2347', 65, 55, 35, 40, 40)"
                ),
                {"id": spirit_id, "user_id": user_id, "client_id": uuid.uuid4()},
            )
        await engine.dispose()

    asyncio.run(_seed_at_previous_head())
    assert asyncio.run(_column_type(url, "spirits", "version")) == "integer"
    upgrade_kelin_test_to("head")
    assert asyncio.run(_alembic_current(url)) == [HEAD_REVISION]
    assert asyncio.run(_column_type(url, "spirits", "version")) == "bigint"

    async def _read_after() -> tuple[str, str, int, int]:
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            ai = await conn.execute(
                text("SELECT assertion FROM public.account_consents WHERE id = :id"),
                {"id": consent_ai},
            )
            terms = await conn.execute(
                text("SELECT assertion FROM public.account_consents WHERE id = :id"),
                {"id": consent_terms},
            )
            spirit = await conn.execute(
                text("SELECT version, onboarding_step FROM public.spirits WHERE id = :id"),
                {"id": spirit_id},
            )
            ai_assertion = str(ai.scalar_one())
            terms_assertion = str(terms.scalar_one())
            version, step = spirit.one()
        await engine.dispose()
        return ai_assertion, terms_assertion, int(version), int(step)

    ai_assertion, terms_assertion, version, step = asyncio.run(_read_after())
    assert ai_assertion == "explicitly_accepted"
    assert terms_assertion == "displayed"
    assert version == 1
    assert step == 0
