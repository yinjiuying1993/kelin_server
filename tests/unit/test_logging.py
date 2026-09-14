from io import StringIO
from unittest.mock import patch
from uuid import UUID, uuid4

from app.core.config import Settings
from app.core.db_probe import FailingDatabaseProbe
from app.core.logging import configure_logging, hash_user_id, redact_event_dict
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


def test_redact_replaces_coordinates() -> None:
    event = {
        "route": "/api/v1/feed",
        "latitude": 31.23,
        "longitude": 121.49,
        "address": "中山东一路",
    }
    out = redact_event_dict(event)
    assert out["latitude"] == "[redacted]"
    assert out["longitude"] == "[redacted]"
    assert out["address"] == "[redacted]"
    assert "31.23" not in str(out)
    assert "121.49" not in str(out)


def test_redact_replaces_memory_summary() -> None:
    event = {
        "route": "/api/v1/memories",
        "summary": "用户希望被叫阿年",
        "user_id_hash": "abc",
    }
    out = redact_event_dict(event)
    assert out["summary"] == "[redacted]"
    assert "阿年" not in str(out)
    assert out["route"] == "/api/v1/memories"


def test_redact_replaces_invite_code() -> None:
    event = {"invite_code": "ABCD2345", "route": "/api/v1/spirits"}
    out = redact_event_dict(event)
    assert out["invite_code"] == "[redacted]"
    assert "ABCD2345" not in str(out)
    assert out["route"] == "/api/v1/spirits"


def test_redact_nested_consent_version_and_invite_shaped_value() -> None:
    event = {
        "route": "/api/v1/spirits",
        "body": {
            "name": "甲灵隐私",
            "consents": {"ai_disclosure": {"document_version": "priv-consent-v1"}},
        },
        "note": "code ABCD2345 used",
    }
    out = redact_event_dict(event)
    nested = out["body"]
    assert isinstance(nested, dict)
    assert nested["consents"] == "[redacted]"
    assert nested["name"] == "甲灵隐私"
    assert "priv-consent-v1" not in str(out)
    assert "ABCD2345" not in str(out)
    assert out["note"] == "code [redacted] used"


def test_hash_user_id_is_not_raw_uuid() -> None:
    user_id = uuid4()
    digest = hash_user_id(user_id)
    assert digest != str(user_id)
    assert len(digest) == 64
    assert digest == hash_user_id(UUID(str(user_id)))
    assert digest != hash_user_id(uuid4())


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
