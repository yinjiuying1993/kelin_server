import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

EGG_TRAITS = {
    "warm": ("WARM2345", 65, 55, 35, 40, 40),
    "cold": ("CLDX2345", 35, 45, 70, 60, 55),
    "wild": ("WYLD2345", 50, 50, 50, 50, 50),
}


async def _insert_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _constraint_names(url: str, table: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT conname FROM pg_constraint WHERE conrelid = CAST(:rel AS regclass)"),
            {"rel": f"public.{table}"},
        )
        names = {str(row[0]) for row in rows.fetchall()}
    await engine.dispose()
    return names


async def _columns(url: str, table: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table"
            ),
            {"table": table},
        )
        names = {str(row[0]) for row in rows.fetchall()}
    await engine.dispose()
    return names


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


def test_identity_tables_have_spec_columns_and_constraints() -> None:
    url = upgrade_empty_kelin_test_to_head()
    spirit_cols = asyncio.run(_columns(url, "spirits"))
    consent_cols = asyncio.run(_columns(url, "account_consents"))
    pref_cols = asyncio.run(_columns(url, "user_preferences"))
    assert {"user_id", "egg", "onboarding_step", "invite_code", "client_id"} <= spirit_cols
    assert {
        "consent_type",
        "document_version",
        "user_id",
        "client_id",
        "assertion",
        "withdrawn_at",
    } <= consent_cols
    assert {"user_id", "dnd_start", "dnd_end", "timezone", "version"} <= pref_cols

    spirit_cons = asyncio.run(_constraint_names(url, "spirits"))
    consent_cons = asyncio.run(_constraint_names(url, "account_consents"))
    assert "ck_spirits_egg" in spirit_cons
    assert "ck_spirits_onboarding_step" in spirit_cons
    assert "ck_spirits_onboarding_complete" in spirit_cons
    assert "uq_spirits_user_id_client_id" in spirit_cons
    assert "ck_account_consents_type" in consent_cons
    assert "uq_account_consents_user_type_version" in consent_cons
    assert "ck_account_consents_assertion" in consent_cons
    assert "ck_account_consents_assertion_matches_type" in consent_cons
    assert "ck_account_consents_ai_disclosure_not_withdrawn" in consent_cons
    pref_cons = asyncio.run(_constraint_names(url, "user_preferences"))
    assert "ck_user_preferences_timezone_len" in pref_cons
    assert "ck_user_preferences_default_city_len" in pref_cons
    assert "ck_user_preferences_version_positive" in pref_cons


def test_egg_enum_and_initial_trait_values_can_be_stored() -> None:
    url = upgrade_empty_kelin_test_to_head()

    async def _run() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            for egg, (
                invite,
                closeness,
                curiosity,
                sharpness,
                nocturnal,
                stubborn,
            ) in EGG_TRAITS.items():
                user_id = uuid.uuid4()
                await conn.execute(
                    text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id}
                )
                await conn.execute(
                    text(
                        "INSERT INTO public.spirits ("
                        "user_id, client_id, egg, invite_code, "
                        "closeness, curiosity, sharpness, nocturnal, stubborn"
                        ") VALUES ("
                        ":user_id, :client_id, :egg, :invite, "
                        ":closeness, :curiosity, :sharpness, :nocturnal, :stubborn)"
                    ),
                    {
                        "user_id": user_id,
                        "client_id": uuid.uuid4(),
                        "egg": egg,
                        "invite": invite,
                        "closeness": closeness,
                        "curiosity": curiosity,
                        "sharpness": sharpness,
                        "nocturnal": nocturnal,
                        "stubborn": stubborn,
                    },
                )
        await engine.dispose()

    asyncio.run(_run())
    bad_user = uuid.uuid4()
    asyncio.run(_insert_user(url, bad_user))
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.spirits ("
            "user_id, client_id, egg, invite_code, "
            "closeness, curiosity, sharpness, nocturnal, stubborn"
            ") VALUES (:user_id, :client_id, 'hot', 'HTXX2345', 50, 50, 50, 50, 50)",
            {"user_id": bad_user, "client_id": uuid.uuid4()},
        )
    )


def test_one_spirit_per_user_and_onboarding_complete_invariant() -> None:
    url = upgrade_empty_kelin_test_to_head()
    user_id = uuid.uuid4()
    asyncio.run(_insert_user(url, user_id))

    async def _insert_first() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO public.spirits ("
                    "user_id, client_id, egg, invite_code, "
                    "closeness, curiosity, sharpness, nocturnal, stubborn"
                    ") VALUES (:user_id, :client_id, 'wild', 'WYLD2346', 50, 50, 50, 50, 50)"
                ),
                {"user_id": user_id, "client_id": uuid.uuid4()},
            )
        await engine.dispose()

    asyncio.run(_insert_first())
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.spirits ("
            "user_id, client_id, egg, invite_code, "
            "closeness, curiosity, sharpness, nocturnal, stubborn"
            ") VALUES (:user_id, :client_id, 'warm', 'WARM2346', 65, 55, 35, 40, 40)",
            {"user_id": user_id, "client_id": uuid.uuid4()},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "UPDATE public.spirits SET onboarding_completed_at = now() WHERE user_id = :user_id",
            {"user_id": user_id},
        )
    )


def test_consent_unique_and_type_check() -> None:
    url = upgrade_empty_kelin_test_to_head()
    user_id = uuid.uuid4()
    asyncio.run(_insert_user(url, user_id))

    async def _insert_first() -> None:
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
        await engine.dispose()

    asyncio.run(_insert_first())
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.account_consents ("
            "user_id, consent_type, document_version, client_id, assertion"
            ") VALUES (:user_id, 'ai_disclosure', 'v1', :client_id, 'explicitly_accepted')",
            {"user_id": user_id, "client_id": uuid.uuid4()},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.account_consents ("
            "user_id, consent_type, document_version, client_id, assertion"
            ") VALUES (:user_id, 'marketing', 'v1', :client_id, 'displayed')",
            {"user_id": user_id, "client_id": uuid.uuid4()},
        )
    )
