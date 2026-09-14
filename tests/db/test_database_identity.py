from app.db.identity import assert_disposable_kelin_test_database
from pytest import raises

TEST_URL = "postgresql+asyncpg://kelin_test:kelin_test@postgres-test:5432/kelin_test"


def test_accepts_explicit_kelin_test_identity() -> None:
    ident = assert_disposable_kelin_test_database(TEST_URL, "test")
    assert ident["database"] == "kelin_test"
    assert ident["username"] == "kelin_test"
    assert ident["host"] == "postgres-test"
    assert "password" not in ident


def test_rejects_prod_looking_host() -> None:
    with raises(ValueError, match="production-looking"):
        assert_disposable_kelin_test_database(
            "postgresql+asyncpg://kelin_test:kelin_test@prod-db.example:5432/kelin_test",
            "test",
        )


def test_rejects_wrong_database_name() -> None:
    with raises(ValueError, match="kelin_test"):
        assert_disposable_kelin_test_database(
            "postgresql+asyncpg://kelin_test:kelin_test@postgres-test:5432/postgres",
            "test",
        )


def test_rejects_non_test_app_env() -> None:
    with raises(ValueError, match="APP_ENV=test"):
        assert_disposable_kelin_test_database(TEST_URL, "prod")
