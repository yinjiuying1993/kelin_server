from pathlib import Path

from app.core.config import Settings, get_settings
from pydantic import SecretStr, ValidationError
from pytest import MonkeyPatch, raises


def test_test_env_allows_missing_database_url(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DATABASE_URL_API", raising=False)
    monkeypatch.chdir(tmp_path)
    settings = Settings(app_env="test")
    assert settings.database_url_api is None
    assert settings.config_presence()["database_url_api"] == "unset"


def test_prod_requires_database_url(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL_API", raising=False)
    with raises(ValidationError):
        Settings(app_env="prod")


def test_prod_requires_jwt_issuer_audience_and_jwks(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL_API", raising=False)
    with raises(ValidationError):
        Settings(
            app_env="prod",
            database_url_api=SecretStr("postgresql://probe:probe@127.0.0.1:1/kelin"),
        )


def test_jwt_config_presence_does_not_include_values() -> None:
    settings = Settings(
        app_env="dev",
        supabase_jwt_issuer="https://secret-project.supabase.co/auth/v1",
        supabase_jwt_audience="authenticated",
        supabase_jwks_url="https://secret-project.supabase.co/auth/v1/.well-known/jwks.json",
    )
    dumped = str(settings.config_presence())
    assert settings.config_presence()["supabase_jwt_issuer"] == "set"
    assert settings.config_presence()["supabase_jwks_url"] == "set"
    assert "secret-project" not in dumped
    assert "https://" not in dumped


def test_empty_database_url_is_treated_as_unset() -> None:
    settings = Settings.model_validate({"app_env": "dev", "database_url_api": ""})
    assert settings.database_url_api is None
    assert settings.config_presence()["database_url_api"] == "unset"


def test_config_presence_never_includes_secret_value() -> None:
    settings = Settings(
        app_env="dev",
        database_url_api=SecretStr("postgresql://probe:super-secret@127.0.0.1:5432/kelin"),
    )
    presence = settings.config_presence()
    dumped = str(presence)
    assert presence["database_url_api"] == "set"
    assert "super-secret" not in dumped
    assert "postgresql://" not in dumped


def test_get_settings_reads_app_env(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    settings = get_settings()
    assert settings.app_env == "dev"


def test_sqlalchemy_async_url_upgrades_plain_postgres_scheme() -> None:
    settings = Settings(
        app_env="test",
        database_url_api=SecretStr(
            "postgresql://kelin_test:kelin_test@postgres-test:5432/kelin_test"
        ),
    )
    url = settings.sqlalchemy_async_url()
    assert url.startswith("postgresql+asyncpg://")
    assert url.endswith("@postgres-test:5432/kelin_test")


def test_sqlalchemy_async_url_rejects_non_postgres() -> None:
    settings = Settings(
        app_env="test",
        database_url_api=SecretStr("mysql://kelin_test:kelin_test@localhost/kelin_test"),
    )
    with raises(ValueError, match="postgresql"):
        settings.sqlalchemy_async_url()


def test_prod_requires_cursor_hmac_secret(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("CURSOR_HMAC_SECRET", raising=False)
    with raises(ValidationError):
        Settings(
            app_env="prod",
            database_url_api=SecretStr("postgresql://probe:probe@127.0.0.1:1/kelin"),
            supabase_jwt_issuer="https://example.supabase.co/auth/v1",
            supabase_jwt_audience="authenticated",
            supabase_jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
        )


def test_cursor_hmac_presence_does_not_include_value() -> None:
    settings = Settings(
        app_env="dev",
        cursor_hmac_secret=SecretStr("super-secret-cursor-key"),
    )
    presence = settings.config_presence()
    dumped = str(presence)
    assert presence["cursor_hmac_secret"] == "set"
    assert "super-secret-cursor-key" not in dumped
    assert settings.cursor_signing_key() == b"super-secret-cursor-key"


def test_test_env_has_dev_cursor_signing_key() -> None:
    settings = Settings(app_env="test")
    assert settings.config_presence()["cursor_hmac_secret"] == "unset"
    assert settings.cursor_signing_key() == b"kelin-dev-cursor-hmac"


def test_empty_bailian_key_is_unset_and_absent_from_presence() -> None:
    settings = Settings.model_validate({"app_env": "dev", "bailian_api_key": ""})
    assert settings.bailian_api_key is None
    presence = settings.config_presence()
    assert presence["bailian_api_key"] == "unset"
    assert presence["bailian_chat_model"] == "unset"
    assert presence["bailian_app_id"] == "unset"


def test_bailian_presence_never_includes_secret_value() -> None:
    secret = "test-only-bailian-key"
    settings = Settings(
        app_env="dev",
        bailian_api_key=SecretStr(secret),
        bailian_workspace_id=SecretStr("ws-secret"),
        bailian_app_id="secret-app-xyz",
        bailian_chat_model="chat",
    )
    presence = settings.config_presence()
    dumped = str(presence)
    assert presence["bailian_api_key"] == "set"
    assert presence["bailian_workspace_id"] == "set"
    assert presence["bailian_app_id"] == "set"
    assert presence["bailian_chat_model"] == "set"
    assert secret not in dumped
    assert "ws-secret" not in dumped
    assert "secret-app-xyz" not in dumped


def test_bailian_tts_voice_presence_is_set_or_unset_only() -> None:
    voice = "vendor-voice-xyz"
    settings = Settings(app_env="dev", bailian_tts_voice=voice)
    presence = settings.config_presence()
    assert presence["bailian_tts_voice"] == "set"
    assert voice not in str(presence)
    empty = Settings.model_validate({"app_env": "dev", "bailian_tts_voice": ""})
    assert empty.bailian_tts_voice is None
    assert empty.config_presence()["bailian_tts_voice"] == "unset"
