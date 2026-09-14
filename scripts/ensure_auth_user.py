"""Insert JWT sub into local auth.users. Dev/test 联调 only; does not create a Supabase user."""

from __future__ import annotations

import argparse
import asyncio
import uuid

from app.core.config import get_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def ensure_auth_user(user_id: uuid.UUID) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.sqlalchemy_async_url())
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO auth.users (id) VALUES (:id) ON CONFLICT (id) DO NOTHING"),
                {"id": user_id},
            )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ensure one local auth.users row for 联调.")
    parser.add_argument("user_id", help="JWT sub UUID")
    args = parser.parse_args(argv)
    asyncio.run(ensure_auth_user(uuid.UUID(args.user_id)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
