from typing import Protocol

from pydantic import SecretStr


class DatabaseUnavailable(Exception):
    """Database probe failed. Message must not include DSN or secrets."""


class DatabaseProbe(Protocol):
    async def ping(self) -> None: ...


class UnsetDatabaseProbe:
    async def ping(self) -> None:
        raise DatabaseUnavailable("DATABASE_URL_API unset")


class FailingDatabaseProbe:
    async def ping(self) -> None:
        raise DatabaseUnavailable("database_unreachable")


class OkDatabaseProbe:
    async def ping(self) -> None:
        return None


class AsyncpgDatabaseProbe:
    def __init__(self, url: SecretStr) -> None:
        self._url = url

    async def ping(self) -> None:
        try:
            import asyncpg  # type: ignore[import-not-found]
        except ImportError:
            raise DatabaseUnavailable("database_driver_unavailable") from None

        connection = None
        try:
            connection = await asyncpg.connect(self._url.get_secret_value(), timeout=2)
            await connection.execute("select 1")
        except DatabaseUnavailable:
            raise
        except Exception:
            raise DatabaseUnavailable("database_unreachable") from None
        finally:
            if connection is not None:
                await connection.close()


def build_database_probe(database_url_api: SecretStr | None) -> DatabaseProbe:
    if database_url_api is None:
        return UnsetDatabaseProbe()
    return AsyncpgDatabaseProbe(database_url_api)
