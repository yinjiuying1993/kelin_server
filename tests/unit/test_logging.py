from io import StringIO
from unittest.mock import patch

from app.core.config import Settings
from app.core.db_probe import FailingDatabaseProbe
from app.core.logging import configure_logging, redact_event_dict
from pydantic import SecretStr


def test_redact_replaces_sensitive_keys() -> None:
    event = {
        "event": "boot",
        "database_url_api": "postgresql://probe:super-secret@127.0.0.1/kelin",
        "route": "/health/ready",
    }
    out = redact_event_dict(event)
    assert out["database_url_api"] == "[redacted]"
    assert out["route"] == "/health/ready"
    assert "super-secret" not in str(out)


def test_redact_replaces_secret_shaped_values() -> None:
    event = {
        "event": "oops",
        "detail": "failed postgresql://probe:super-secret@127.0.0.1/kelin",
    }
    out = redact_event_dict(event)
    assert out["detail"] == "[redacted]"


def test_configure_logging_and_boot_do_not_print_secret() -> None:
    secret = "postgresql://probe:leak-me-now@127.0.0.1:5432/kelin"
    buf = StringIO()
    with patch("sys.stdout", buf):
        configure_logging(Settings(app_env="test"))
        from app.core.logging import get_logger

        get_logger().info(
            "should_redact",
            database_url_api=secret,
            route="/health/ready",
        )
    text = buf.getvalue()
    assert "leak-me-now" not in text
    assert "postgresql://" not in text
    assert "/health/ready" in text


def test_app_boot_log_has_presence_not_dsn() -> None:
    secret = "postgresql://probe:leak-me-now@127.0.0.1:5432/kelin"
    buf = StringIO()
    with patch("sys.stdout", buf):
        from app.main import create_app

        create_app(
            Settings(app_env="dev", database_url_api=SecretStr(secret)),
            db_probe=FailingDatabaseProbe(),
        )
    text = buf.getvalue()
    assert "leak-me-now" not in text
    assert '"database_url_api": "set"' in text
