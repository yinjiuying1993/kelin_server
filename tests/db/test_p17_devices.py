"""P17-T01 device upsert, rotation, disable, and environment mismatch."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.devices import decrypt_apns_token, hash_apns_token
from app.schemas.devices import RegisterDeviceRequest
from app.services.devices import register_device
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 12, 11, 0, tzinfo=UTC)
TOKEN_A = "a" * 64
TOKEN_B = "b" * 64


async def _insert_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


def _body(
    token: str,
    installation: uuid.UUID,
    *,
    enabled: bool = True,
    environment: str = "sandbox",
) -> RegisterDeviceRequest:
    return RegisterDeviceRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "installation_id": str(installation),
            "apns_token": token,
            "environment": environment,
            "enabled": enabled,
            "app_version": "1.0.0",
            "locale": "zh-Hans",
        }
    )


def test_device_upsert_rotate_disable_and_environment() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_devices(url))


async def _assert_devices(url: str) -> None:
    settings = Settings(app_env="test", database_url_api=SecretStr(url))
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    await _insert_user(url, user_a)
    await _insert_user(url, user_b)
    installation = uuid.uuid4()
    owner_a = CurrentUser(id=user_a)
    owner_b = CurrentUser(id=user_b)
    async with claimed_transaction(factory, owner_a) as session:
        first = await register_device(
            session, owner_a, _body(TOKEN_A, installation), now=NOW, settings=settings
        )
        again = await register_device(
            session, owner_a, _body(TOKEN_A, installation), now=NOW, settings=settings
        )
        rotated = await register_device(
            session, owner_a, _body(TOKEN_B, installation), now=NOW, settings=settings
        )
        disabled = await register_device(
            session,
            owner_a,
            _body(TOKEN_B, installation, enabled=False),
            now=NOW,
            settings=settings,
        )
    assert first.device_id == again.device_id == rotated.device_id == disabled.device_id
    assert first.enabled is True
    assert disabled.enabled is False
    async with claimed_transaction(factory, owner_a) as session:
        try:
            await register_device(
                session,
                owner_a,
                _body(TOKEN_B, installation, environment="production"),
                now=NOW,
                settings=settings,
            )
            raise AssertionError("production token must be rejected in test")
        except ApiError as exc:
            assert exc.code == "INVALID_INPUT"
    other_install = uuid.uuid4()
    async with claimed_transaction(factory, owner_b) as session:
        stolen = await register_device(
            session, owner_b, _body(TOKEN_B, other_install), now=NOW, settings=settings
        )
    assert stolen.device_id != first.device_id
    inspect = create_async_engine(url)
    async with inspect.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT user_id, apns_token_hash, apns_token_encrypted, "
                    "notifications_enabled, invalidated_at, environment "
                    "FROM public.devices"
                )
            )
        ).mappings().all()
    await inspect.dispose()
    hashes = {str(row["apns_token_hash"]) for row in rows}
    assert hash_apns_token(TOKEN_B) in hashes
    assert hash_apns_token(TOKEN_A) not in hashes
    for row in rows:
        cipher = str(row["apns_token_encrypted"])
        assert TOKEN_A not in cipher
        assert TOKEN_B not in cipher
        assert row["environment"] == "sandbox"
        if row["user_id"] == user_a:
            assert row["notifications_enabled"] is False
            assert row["invalidated_at"] is not None
            assert str(row["apns_token_hash"]) != hash_apns_token(TOKEN_B)
        if row["user_id"] == user_b:
            assert row["invalidated_at"] is None
            assert row["notifications_enabled"] is True
            assert str(row["apns_token_hash"]) == hash_apns_token(TOKEN_B)
            assert (
                decrypt_apns_token(cipher, settings.device_token_fernet_key()) == TOKEN_B
            )
    await engine.dispose()
