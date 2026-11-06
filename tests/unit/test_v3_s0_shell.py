from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain import ask as ask_domain
from app.domain import craft as craft_domain
from app.domain import craft_ledger as ledger_domain
from app.domain import match_index as index_domain
from app.main import create_app
from app.repositories import ask as ask_repo
from app.repositories import craft as craft_repo
from app.repositories import craft_ledger as ledger_repo
from app.repositories import match_index as index_repo

_SHELLS = (
    "/api/v1/v3/craft",
    "/api/v1/v3/match-index",
    "/api/v1/v3/ask",
    "/api/v1/v3/craft-ledger",
    "/api/v1/v3/craft-settle",
)


def test_v3_packages_import() -> None:
    assert ask_domain.__doc__
    assert craft_domain.__doc__
    assert ledger_domain.__doc__
    assert index_domain.__doc__
    assert ask_repo.__doc__
    assert craft_repo.__doc__
    assert ledger_repo.__doc__
    assert index_repo.__doc__


def test_v3_shells_are_not_implemented_and_hidden_from_openapi() -> None:
    application = create_app(Settings(app_env="test"))
    paths = set(application.openapi().get("paths", {}))
    assert not any(path.startswith("/api/v1/v3/") for path in paths)
    client = TestClient(application)
    for path in _SHELLS:
        response = client.get(path)
        assert response.status_code == 501
        body = response.json()
        assert body["error"]["code"] == "NOT_IMPLEMENTED"
        assert body["error"]["message"] == "not implemented"
