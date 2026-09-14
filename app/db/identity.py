"""Parse and guard disposable test database identity. Never log passwords."""

from sqlalchemy.engine.url import make_url

KELIN_TEST_DB_NAME = "kelin_test"
KELIN_TEST_DB_USER = "kelin_test"


def database_identity(url: str) -> dict[str, str]:
    parsed = make_url(url)
    return {
        "driver": parsed.drivername,
        "username": parsed.username or "",
        "host": parsed.host or "",
        "port": str(parsed.port or ""),
        "database": parsed.database or "",
    }


def assert_disposable_kelin_test_database(url: str, app_env: str) -> dict[str, str]:
    if app_env != "test":
        raise ValueError("destructive Alembic tests require APP_ENV=test")
    ident = database_identity(url)
    host = ident["host"].lower()
    if "prod" in host or ident["database"] in {"prod", "production"}:
        raise ValueError("refusing Alembic against a production-looking database")
    if ident["database"] != KELIN_TEST_DB_NAME:
        raise ValueError("refusing Alembic: database name must be kelin_test")
    if ident["username"] != KELIN_TEST_DB_USER:
        raise ValueError("refusing Alembic: username must be kelin_test")
    if not ident["driver"].startswith("postgresql"):
        raise ValueError("refusing Alembic: driver must be postgresql")
    return ident
