from app.core.config import Settings, get_settings
from pydantic import SecretStr, ValidationError
from pytest import MonkeyPatch, raises


def test_test_env_allows_missing_database_url() -> None:
    settings = Settings(app_env="test")
    assert settings.database_url_api is None
    assert settings.config_presence()["database_url_api"] == "unset"


def test_prod_requires_database_url() -> None:
    with raises(ValidationError):
        Settings(app_env="prod")


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
