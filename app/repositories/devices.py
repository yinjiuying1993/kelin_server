"""Device registration persistence. Spec §§7.4, 14.6."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class DeviceRow:
    device_id: uuid.UUID
    enabled: bool
    updated_at: datetime


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("devices cannot write in a read-only transaction")


async def upsert_device(
    session: AsyncSession,
    *,
    installation_id: uuid.UUID,
    token_hash: str,
    token_encrypted: str,
    environment: str,
    enabled: bool,
    app_version: str | None,
    locale: str | None,
    now: datetime,
) -> DeviceRow:
    row = (
        await session.execute(
            text(
                "SELECT device_id, enabled, updated_at FROM private.upsert_device("
                ":installation_id, :token_hash, :token_encrypted, :environment, "
                ":enabled, :app_version, :locale, :now)"
            ),
            {
                "installation_id": installation_id,
                "token_hash": token_hash,
                "token_encrypted": token_encrypted,
                "environment": environment,
                "enabled": enabled,
                "app_version": app_version,
                "locale": locale,
                "now": now,
            },
        )
    ).one()
    return DeviceRow(device_id=row.device_id, enabled=bool(row.enabled), updated_at=row.updated_at)
